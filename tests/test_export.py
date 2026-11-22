"""The audit export (app/export.py): every document of the period with its
original, its data, its lines and its whole history, nothing of another
period or tenant, and a checksum for every file."""

import csv
import hashlib
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, select

from app import db, safe_xml
from app.cli import main as cli
from app.export import export, extension
from app.ingest import ingest
from app.retention import run as retention_run
from app.settings import Settings
from app.tenants import update_config
from tests.support import PDF, PDF_DOCUMENT, XML, inline_queue

SETTINGS = Settings.from_env()
YEAR_2016 = (date(2016, 1, 1), date(2016, 12, 31))  # the sample XML is from 4 April 2016
PDF_DAY = PDF_DOCUMENT.invoice.issue_date
ALL = (date(2000, 1, 1), date(2099, 12, 31))


def stored(session_factory, data: bytes, name: str, tenant_id: str = "default") -> str:
    return ingest(
        session_factory, inline_queue(session_factory), data, name, tenant_id=tenant_id
    ).document_id


@pytest.fixture
def documents(session_factory):
    return stored(session_factory, XML, "r.xml"), stored(session_factory, PDF, "r.pdf")


def read_csv(path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file, delimiter=";"))


def run_export(session_factory, folder, period=ALL, tenant="default"):
    return export(session_factory, SETTINGS, tenant, *period, folder)


def test_every_document_of_the_period_is_in_it_with_its_original(
    session_factory, documents, tmp_path
):
    done = run_export(session_factory, tmp_path / "out")
    rows = {row["beleg_id"]: row for row in read_csv(tmp_path / "out" / "belege.csv")}
    assert set(rows) == set(documents)
    with session_factory() as session:
        for document_id in documents:
            document = session.get(db.DocumentRow, document_id)
            row = rows[document_id]
            original = (tmp_path / "out" / row["datei"]).read_bytes()
            assert original == document.content
            assert row["sha256"] == document.sha256 == hashlib.sha256(original).hexdigest()
            assert row["protokoll_kopf"] == document.audit_head
    assert (done.documents, done.problems) == (2, [])


def test_its_lines_and_history_are_all_there(session_factory, documents, tmp_path):
    run_export(session_factory, tmp_path / "out")
    lines = read_csv(tmp_path / "out" / "positionen.csv")
    history = read_csv(tmp_path / "out" / "protokoll.csv")
    with session_factory() as session:
        for document_id in documents:
            document = session.get(db.DocumentRow, document_id)
            data = (document.invoice or document.draft).data
            mine = [row for row in lines if row["beleg_id"] == document_id]
            assert [row["bezeichnung"] for row in mine] == [
                line["description"] for line in data["lines"]
            ]
            events = [row for row in history if row["beleg_id"] == document_id]
            assert [(row["ereignis"], row["hash"]) for row in events] == [
                (event.event, event.hash) for event in document.audit_events
            ]


def test_amounts_and_dates_are_written_the_german_way(session_factory, documents, tmp_path):
    run_export(session_factory, tmp_path / "out")
    (row,) = [r for r in read_csv(tmp_path / "out" / "belege.csv") if r["datei"].endswith(".xml")]
    assert (row["belegnummer"], row["belegdatum"], row["brutto"]) == (
        "123456XX",
        "04.04.2016",
        "336,9",
    )
    assert row["aufbewahren_bis"] == f"31.12.{datetime.now(UTC).year + 8}"
    raw = (tmp_path / "out" / "belege.csv").read_bytes()
    assert b"\r\n" in raw
    raw.decode("utf-8")


def test_only_the_period_is_exported(session_factory, documents, tmp_path):
    xml_id, pdf_id = documents
    done = run_export(session_factory, tmp_path / "2016", YEAR_2016)
    assert [r["beleg_id"] for r in read_csv(tmp_path / "2016" / "belege.csv")] == [xml_id]
    assert done.documents == 1
    done = run_export(session_factory, tmp_path / "pdf", (PDF_DAY, PDF_DAY))
    assert [r["beleg_id"] for r in read_csv(tmp_path / "pdf" / "belege.csv")] == [pdf_id]
    assert len(list((tmp_path / "pdf" / "originals").iterdir())) == 1


def test_every_file_has_its_checksum(session_factory, documents, tmp_path):
    folder = tmp_path / "out"
    run_export(session_factory, folder)
    listed = {}
    for line in (folder / "checksums.sha256").read_text().splitlines():
        digest, name = line.split("  ", 1)
        listed[name] = digest
    files = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()} - {
        "checksums.sha256"
    }
    assert set(listed) == files
    assert all(
        hashlib.sha256((folder / name).read_bytes()).hexdigest() == d for name, d in listed.items()
    )


def test_the_index_describes_each_table_as_its_file_is(session_factory, documents, tmp_path):
    folder = tmp_path / "out"
    run_export(session_factory, folder, YEAR_2016)
    text = (folder / "index.xml").read_text(encoding="utf-8")
    assert '<!DOCTYPE DataSet SYSTEM "gdpdu-01-09-2004.dtd">' in text
    # Our own parser refuses any DOCTYPE (it reads files from outside): checked
    # above, the rest is parsed without it.
    doctype = '<!DOCTYPE DataSet SYSTEM "gdpdu-01-09-2004.dtd">\n'
    root = safe_xml.parse(text.replace(doctype, "").encode())
    tables = root.findall("./Media/Table")
    assert [t.findtext("URL") for t in tables] == [
        "belege.csv", "positionen.csv", "protokoll.csv", "loeschungen.csv",
    ]  # fmt: skip
    for table in tables:
        described = [
            c.findtext("Name") for c in table.find("VariableLength") if c.find("Name") is not None
        ]
        with (folder / table.findtext("URL")).open(encoding="utf-8") as file:
            assert described == next(csv.reader(file, delimiter=";"))
        assert table.findtext("Validity/Range/From") == "01.01.2016"
        assert table.findtext("DecimalSymbol") == ","


def test_another_tenants_documents_are_not_in_it(session_factory, documents, tmp_path):
    with session_factory.begin() as session:
        session.add(db.TenantRow(id="b", slug="b", name="B GmbH"))
    theirs = stored(session_factory, XML, "b.xml", tenant_id="b")
    run_export(session_factory, tmp_path / "default")
    run_export(session_factory, tmp_path / "b", tenant="b")
    assert theirs not in {r["beleg_id"] for r in read_csv(tmp_path / "default" / "belege.csv")}
    assert [r["beleg_id"] for r in read_csv(tmp_path / "b" / "belege.csv")] == [theirs]


def test_deletions_of_the_period_are_in_it(session_factory, tmp_path):
    gone = stored(session_factory, XML, "r.xml")
    with session_factory.begin() as session:
        update_config(session, "default", {"deletion_enabled": True}, SETTINGS)
    retention_run(session_factory, SETTINGS, date(2035, 1, 1), apply=True)
    run_export(session_factory, tmp_path / "out")
    (row,) = read_csv(tmp_path / "out" / "loeschungen.csv")
    assert (row["beleg_id"], row["geloescht_von"]) == (gone, "retention")
    assert read_csv(tmp_path / "out" / "belege.csv") == []


def test_a_broken_history_is_named_in_the_export(session_factory, documents, tmp_path):
    xml_id, _ = documents
    events = db.AuditEventRow.__table__
    with session_factory.begin() as session:
        last = session.scalars(
            select(db.AuditEventRow.seq)
            .where(db.AuditEventRow.document_id == xml_id)
            .order_by(db.AuditEventRow.seq.desc())
        ).first()
        session.execute(delete(events).where(events.c.seq == last))
    done = run_export(session_factory, tmp_path / "out")
    assert done.problems == [f"default {xml_id}: history_cut"]
    assert f"default {xml_id}: history_cut" in (tmp_path / "out" / "manifest.txt").read_text()


def test_an_export_goes_into_a_new_or_empty_folder(session_factory, documents, tmp_path):
    (tmp_path / "used").mkdir()
    (tmp_path / "used" / "x").write_text("x")
    with pytest.raises(ValueError, match="not empty"):
        run_export(session_factory, tmp_path / "used")
    with pytest.raises(ValueError, match="no tenant"):
        run_export(session_factory, tmp_path / "a", tenant="nobody")
    with pytest.raises(ValueError, match="ends before"):
        run_export(session_factory, tmp_path / "b", (date(2026, 2, 1), date(2026, 1, 1)))


def test_originals_get_the_extension_of_what_they_are():
    assert [extension(x) for x in (PDF, XML, b"\xff\xd8\xff\xe0", b"\x89PNG", b"hello")] == [
        "pdf", "xml", "jpg", "png", "bin",
    ]  # fmt: skip


def test_export_on_the_command_line(session_factory, documents, tmp_path, capsys):
    args = ["export", str(tmp_path / "out"), "--from", "2000-01-01", "--to", "2099-12-31"]
    assert cli(args, session_factory=session_factory) == 0
    out = capsys.readouterr().out
    assert "2 documents, " in out
    assert "not encrypted" in out
    assert cli(args, session_factory=session_factory) == 2  # the folder is not empty now
