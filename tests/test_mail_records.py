"""Mail that is not (only) an invoice (v1.0 Stage 3, increment 2): ZIPs one
level deep within limits, encrypted PDFs, oversized, empty and unsupported
files, and mails with nothing attached — each kept with its reason, listed,
never silently dropped."""

import io
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from app import db
from app.api.main import create_app
from app.auth import Role, create_token, create_user
from app.mailbox import ZIP_MAX_MEMBERS
from app.tenants import create_tenant
from tests.support import PDF, XML, inline_queue, settings_for
from tests.test_mailbox import Mailbox, mail, pdf, read


def zipped(*members) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


def attached_zip(data, name="rechnungen.zip"):
    return (name, data, "application", "zip", False)


def records(session_factory) -> list[db.MailRow]:
    with session_factory() as session:
        return session.query(db.MailRow).order_by(db.MailRow.uid).all()


def outcomes(record) -> list[tuple]:
    return [(p["filename"], p["outcome"], p["reason"]) for p in record.parts]


def stored(session_factory) -> list[str]:
    with session_factory() as session:
        return sorted(d.filename for d in session.query(db.DocumentRow).all())


def test_a_mail_with_nothing_attached_is_kept_not_dropped(session_factory):
    done = read(session_factory, Mailbox([mail("Frage zur Rechnung", sender="kunde@example.test")]))
    (record,) = records(session_factory)
    assert (done.without_invoice, record.subject, record.sender, record.parts) == (
        1,
        "Frage zur Rechnung",
        "kunde@example.test",
        [],
    )


def test_every_invoice_in_a_zip_arrives_and_the_rest_is_refused_with_a_reason(session_factory):
    archive = zipped(("2026/rechnung.pdf", PDF), ("rechnung.xml", XML), ("liesmich.txt", b"Hallo"))
    read(session_factory, Mailbox([mail("Rechnungen", [attached_zip(archive)])]))
    assert stored(session_factory) == ["rechnung.pdf", "rechnung.xml"]
    (record,) = records(session_factory)
    assert sorted(outcomes(record)) == [
        ("liesmich.txt", "refused", "unsupported_type"),
        ("rechnung.pdf", "ingested", None),
        ("rechnung.xml", "ingested", None),
    ]


def test_a_zip_inside_a_zip_is_not_opened(session_factory):
    inner = zipped(("rechnung.pdf", PDF))
    read(session_factory, Mailbox([mail("z", [attached_zip(zipped(("innen.zip", inner)))])]))
    (record,) = records(session_factory)
    assert outcomes(record) == [("innen.zip", "refused", "nested_zip")]
    assert stored(session_factory) == []


def test_a_zip_with_too_many_files_is_refused_whole(session_factory):
    many = zipped(*((f"r{n}.xml", XML + str(n).encode()) for n in range(ZIP_MAX_MEMBERS + 1)))
    read(session_factory, Mailbox([mail("viele", [attached_zip(many)])]))
    (record,) = records(session_factory)
    assert record.parts[0]["reason"].startswith("zip_too_many_files")
    assert stored(session_factory) == []


def test_a_zip_bomb_is_refused_without_unpacking_it_whole(session_factory):
    # 20 MB of one byte packs to a few kilobytes: far beyond any invoice's ratio.
    bomb = zipped(("rechnung.xml", b"0" * 20_000_000))
    assert len(bomb) < 100_000
    read(session_factory, Mailbox([mail("bombe", [attached_zip(bomb)])]), max_bytes=30_000_000)
    (record,) = records(session_factory)
    assert outcomes(record) == [("rechnung.xml", "refused", "zip_bomb")]


def test_a_member_larger_than_the_limit_is_read_no_further(session_factory):
    big = zipped(("rechnung.pdf", PDF + b"%" + b"x" * 5000))
    read(session_factory, Mailbox([mail("gross", [attached_zip(big)])]), max_bytes=2000)
    (record,) = records(session_factory)
    assert outcomes(record)[0][1:] == ("refused", "too_large")
    # Read with the limit: no more than one byte past it.
    assert record.parts[0]["bytes"] == 2001


def marked_encrypted(archive: bytes) -> bytes:
    """Python cannot write encrypted ZIPs; set the "encrypted" bit in the
    local and central headers, which is what a reader goes by."""
    data = bytearray(archive)
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = data.find(signature)
        while at != -1:
            data[at + offset] |= 0x1
            at = data.find(signature, at + 4)
    return bytes(data)


def test_an_encrypted_zip_member_is_refused(session_factory):
    locked = marked_encrypted(zipped(("rechnung.pdf", PDF)))
    read(session_factory, Mailbox([mail("verschluesselt", [attached_zip(locked)])]))
    (record,) = records(session_factory)
    assert outcomes(record) == [("rechnung.pdf", "refused", "encrypted_zip")]


def test_a_broken_zip_is_refused_as_unreadable(session_factory):
    read(session_factory, Mailbox([mail("kaputt", [attached_zip(b"PK\x03\x04 not a zip")])]))
    (record,) = records(session_factory)
    assert outcomes(record) == [("rechnungen.zip", "refused", "unreadable")]


def encrypted(pdf_bytes: bytes) -> bytes:
    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(pdf_bytes)).pages:
        writer.add_page(page)
    writer.encrypt("geheim")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_a_pdf_that_needs_a_password_is_refused_not_stored(session_factory):
    read(session_factory, Mailbox([mail("pdf", [pdf("gesperrt.pdf", encrypted(PDF))])]))
    (record,) = records(session_factory)
    assert outcomes(record) == [("gesperrt.pdf", "refused", "encrypted_pdf")]
    assert stored(session_factory) == []


def test_unsupported_and_empty_attachments_are_refused(session_factory):
    word = ("angebot.docx", b"PK\x03\x04docx", "application", "octet-stream", False)
    empty = ("leer.pdf", b"", "application", "pdf", False)
    read(session_factory, Mailbox([mail("anderes", [word, empty, pdf()])]))
    (record,) = records(session_factory)
    assert sorted(outcomes(record)) == [
        ("angebot.docx", "refused", "unsupported_type"),
        ("leer.pdf", "refused", "empty"),
        ("rechnung.pdf", "ingested", None),
    ]


def test_an_invoice_already_known_is_listed_as_such(session_factory):
    read(session_factory, Mailbox([mail("a", [pdf()]), mail("b", [pdf("kopie.pdf")])]))
    second = records(session_factory)[1]
    assert outcomes(second) == [("kopie.pdf", "known", None)]
    assert second.parts[0]["document_id"] == records(session_factory)[0].parts[0]["document_id"]


def test_a_mail_is_recorded_exactly_once_even_after_a_crash(session_factory):
    box = Mailbox([mail("a", [pdf()])])
    box.fail_marking = True
    with pytest.raises(Exception, match="connection lost"):
        read(session_factory, box)
    assert records(session_factory) == []
    box.fail_marking = False
    read(session_factory, box)
    read(session_factory, box)
    assert len(records(session_factory)) == 1


def viewer_client(session_factory, tmp_path, tenant_id=db.DEFAULT_TENANT_ID):
    settings = replace(settings_for(tmp_path), auth="on")
    client = TestClient(create_app(session_factory, inline_queue(session_factory), settings))
    with session_factory.begin() as session:
        user = create_user(session, f"v@{tenant_id}.example", "V", Role.VIEWER, tenant_id=tenant_id)
        token = create_token(session, user.id, "t", tenant_id)
    return client, {"Authorization": f"Bearer {token}"}


def test_the_mail_list_shows_newest_first_with_the_outcome_of_each_file(session_factory, tmp_path):
    read(session_factory, Mailbox([mail("erste", [pdf()]), mail("zweite")]))
    client, headers = viewer_client(session_factory, tmp_path)
    mails = client.get("/mail", headers=headers).json()["mails"]
    assert [m["subject"] for m in mails] == ["zweite", "erste"]
    assert mails[1]["parts"][0]["outcome"] == "ingested"


def test_each_tenant_sees_only_its_own_mail(session_factory, tmp_path):
    with session_factory.begin() as session:
        acme = create_tenant(session, "acme", "Acme GmbH").id
    read(session_factory, Mailbox([mail("fuer acme", [pdf()])]), tenant_id=acme)
    client, headers = viewer_client(session_factory, tmp_path)
    assert client.get("/mail", headers=headers).json()["mails"] == []


def test_a_mail_read_again_after_a_crash_still_counts_its_invoice_as_received(session_factory):
    """Found by the mailbox eval: stored, then the poller died before marking
    the mail; read again, the invoice is this mail's own, not a copy."""
    box = Mailbox([mail("a", [pdf()])])
    box.fail_marking = True
    with pytest.raises(Exception, match="connection lost"):
        read(session_factory, box)
    box.fail_marking = False
    read(session_factory, box)
    (record,) = records(session_factory)
    assert outcomes(record) == [("rechnung.pdf", "ingested", None)]


def test_the_same_invoice_in_a_later_mail_is_still_known(session_factory):
    box = Mailbox([mail("a", [pdf()]), mail("b", [pdf()])])
    read(session_factory, box)
    assert [outcomes(r)[0][1] for r in records(session_factory)] == ["ingested", "known"]
