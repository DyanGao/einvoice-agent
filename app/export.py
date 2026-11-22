"""Audit export (v1.0 Stage 6, increment 6): a tenant's documents of a period,
as files for a tax audit (Datenträgerüberlassung, § 147 Abs. 6 AO).

    einvoice export OUT_DIR --from 2026-01-01 --to 2026-12-31 [--tenant SLUG]

A document belongs to the period by the date its invoice is from, or, not
read, the day it was received. The folder holds:

    originals/<document id>.<pdf|xml|jpg|png|tif|bin>   each original as received
    belege.csv            one row per document: what it is, what it says, its hashes
    positionen.csv        the invoice lines
    protokoll.csv         every history event, with the hash chain
    loeschungen.csv       documents deleted in the period (the deletion log)
    index.xml             the tables described as the GDPdU description standard has it
    manifest.txt          tenant, period, when, the chain check, the digest
    checksums.sha256      a SHA-256 for every file above (`shasum -a 256 -c checksums.sha256`)

CSV: semicolons, a decimal comma, dates as DD.MM.YYYY, UTF-8, CRLF. The
index follows the GDPdU description standard (gdpdu-01-09-2004.dtd, which the
auditor's software brings); it has not been checked with such software.

The export is not encrypted: it holds every invoice of the period as it
reads. Hand it over on an encrypted medium, and delete it afterwards.
"""

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.chain import instant
from app.db.rls import tenant_transaction
from app.db.tables import DeletionLogRow, DocumentRow, TenantRow
from app.retention import end_of
from app.settings import Settings
from app.tenants import config_for
from app.verify import problems_of

_SIGNATURES = (
    (b"%PDF-", "pdf"),
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG", "png"),
    (b"II*\x00", "tif"),
    (b"MM\x00*", "tif"),
)


def extension(content: bytes) -> str:
    for signature, name in _SIGNATURES:
        if content.startswith(signature):
            return name
    head = content.lstrip(b"\xef\xbb\xbf \t\r\n")[:1]
    return "xml" if head == b"<" else "bin"


def _utc_day(moment: datetime) -> date:
    """The day in UTC (SQLite gives naive datetimes for what was stored as UTC)."""
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC).date()


def _day(value: date | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = _utc_day(value)
    return value.strftime("%d.%m.%Y")


def _amount(value) -> str:
    if value in (None, ""):
        return ""
    try:
        return format(Decimal(str(value)), "f").replace(".", ",")
    except InvalidOperation:
        return str(value)


def _date_of(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Column:
    name: str
    kind: str  # text | number | date
    places: int = 0  # for numbers


DOCUMENTS = (
    Column("beleg_id", "text"),
    Column("eingang", "date"),
    Column("dateiname", "text"),
    Column("datei", "text"),
    Column("format", "text"),
    Column("status", "text"),
    Column("pruefung", "text"),
    Column("stand", "text"),
    Column("belegart", "text"),
    Column("belegnummer", "text"),
    Column("belegdatum", "date"),
    Column("faellig", "date"),
    Column("waehrung", "text"),
    Column("lieferant", "text"),
    Column("lieferant_ustid", "text"),
    Column("kaeufer", "text"),
    Column("bestellnummer", "text"),
    Column("bezugsrechnung", "text"),
    Column("netto", "number", 2),
    Column("steuer", "number", 2),
    Column("brutto", "number", 2),
    Column("zahlbetrag", "number", 2),
    Column("iban", "text"),
    Column("sha256", "text"),
    Column("protokoll_kopf", "text"),
    Column("loeschsperre", "text"),
    Column("aufbewahren_bis", "date"),
)
LINES = (
    Column("beleg_id", "text"),
    Column("position", "number"),
    Column("bezeichnung", "text"),
    Column("menge", "number", 4),
    Column("einzelpreis", "number", 4),
    Column("netto", "number", 2),
    Column("steuersatz", "number", 2),
)
HISTORY = (
    Column("beleg_id", "text"),
    Column("nr", "number"),
    Column("zeitpunkt", "text"),
    Column("ereignis", "text"),
    Column("wer", "text"),
    Column("benutzer_id", "text"),
    Column("angaben", "text"),
    Column("hash_davor", "text"),
    Column("hash", "text"),
)
DELETIONS = (
    Column("beleg_id", "text"),
    Column("sha256", "text"),
    Column("protokoll_kopf", "text"),
    Column("ereignisse", "number"),
    Column("eingang", "date"),
    Column("aufbewahren_bis", "date"),
    Column("geloescht", "date"),
    Column("geloescht_von", "text"),
)
TABLES = {
    "belege.csv": ("Belege", "Eingangsrechnungen und Gutschriften mit ihren Angaben", DOCUMENTS),
    "positionen.csv": ("Positionen", "Rechnungspositionen", LINES),
    "protokoll.csv": ("Protokoll", "Verarbeitungsprotokoll je Beleg mit Hash-Kette", HISTORY),
    "loeschungen.csv": ("Loeschungen", "Nach Ablauf der Aufbewahrung geloeschte Belege", DELETIONS),
}


def _csv(columns: tuple[Column, ...], rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([column.name for column in columns])
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _document_row(document: DocumentRow, file: str, end: date) -> list[str]:
    record = document.invoice if document.invoice is not None else document.draft
    data = {} if record is None else record.data
    seller, buyer = data.get("seller") or {}, data.get("buyer") or {}
    ibans = data.get("payment_ibans") or []
    return [
        document.id,
        _day(document.created_at),
        document.filename or "",
        file,
        document.format,
        document.status,
        document.review_state,
        "festgestellt" if document.invoice is not None else ("gelesen" if data else ""),
        data.get("type_code") or "",
        data.get("number") or "",
        _day(_date_of(data.get("issue_date"))),
        _day(_date_of(data.get("due_date"))),
        data.get("currency") or "",
        seller.get("name") or "",
        seller.get("vat_id") or "",
        buyer.get("name") or "",
        data.get("po_ref") or "",
        data.get("preceding_ref") or "",
        _amount(data.get("net_total")),
        _amount(data.get("vat_total")),
        _amount(data.get("gross_total")),
        _amount(data.get("amount_due")),
        ibans[0] if ibans else "",
        document.sha256,
        document.audit_head or "",
        "ja" if document.legal_hold else "nein",
        _day(end),
    ]


def _line_rows(document: DocumentRow) -> list[list[str]]:
    record = document.invoice if document.invoice is not None else document.draft
    lines = [] if record is None else record.data.get("lines") or []
    return [
        [
            document.id,
            str(position),
            line.get("description") or "",
            _amount(line.get("quantity")),
            _amount(line.get("unit_price")),
            _amount(line.get("net")),
            _amount(line.get("vat_rate")),
        ]
        for position, line in enumerate(lines, start=1)
    ]


def _history_rows(document: DocumentRow) -> list[list[str]]:
    return [
        [
            document.id,
            str(event.seq),
            instant(event.created_at),
            event.event,
            event.actor,
            event.actor_user_id or "",
            json.dumps(event.detail, ensure_ascii=False, sort_keys=True),
            event.prev_hash or "",
            event.hash or "",
        ]
        for event in document.audit_events
    ]


def _index(tenant: str, start: date, end: date) -> bytes:
    """The tables described as the GDPdU description standard has it."""

    def column(c: Column, primary: bool) -> str:
        tag = "VariablePrimaryKey" if primary else "VariableColumn"
        if c.kind == "number":
            kind = (
                "<Numeric>"
                + (f"<Accuracy>{c.places}</Accuracy>" if c.places else "")
                + "</Numeric>"
            )
        elif c.kind == "date":
            kind = "<Date><Format>DD.MM.YYYY</Format></Date>"
        else:
            kind = "<AlphaNumeric/>"
        return f"        <{tag}><Name>{c.name}</Name>{kind}</{tag}>"

    tables = []
    for url, (name, description, columns) in TABLES.items():
        keyed = name in ("Belege", "Loeschungen")
        body = "\n".join(column(c, keyed and i == 0) for i, c in enumerate(columns))
        tables.append(
            f"""    <Table>
      <URL>{url}</URL>
      <Name>{escape(name)}</Name>
      <Description>{escape(description)}</Description>
      <Validity>
        <Range><From>{_day(start)}</From><To>{_day(end)}</To></Range>
        <Format>DD.MM.YYYY</Format>
      </Validity>
      <UTF8/>
      <DecimalSymbol>,</DecimalSymbol>
      <DigitGroupingSymbol>.</DigitGroupingSymbol>
      <VariableLength>
        <ColumnDelimiter>;</ColumnDelimiter>
        <RecordDelimiter>&#13;&#10;</RecordDelimiter>
        <TextEncapsulator>"</TextEncapsulator>
{body}
      </VariableLength>
    </Table>"""
        )
    return (
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE DataSet SYSTEM "gdpdu-01-09-2004.dtd">
<DataSet>
  <Version>1.0</Version>
  <DataSupplier>
    <Name>{escape(tenant)}</Name>
    <Location></Location>
    <Comment>einvoice-agent, Datenträgerüberlassung {_day(start)} bis {_day(end)}</Comment>
  </DataSupplier>
  <Media>
    <Name>Datenträger 1</Name>
{chr(10).join(tables)}
  </Media>
</DataSet>
"""
    ).encode()


@dataclass
class Export:
    folder: Path
    documents: int = 0
    lines: int = 0
    events: int = 0
    deletions: int = 0
    # Breaks found in the exported documents' originals and chains (app/verify.py).
    problems: list[str] = field(default_factory=list)
    digest: str = ""


def in_period(document: DocumentRow, start: date, end: date) -> bool:
    record = document.invoice if document.invoice is not None else document.draft
    dated = _date_of(None if record is None else record.data.get("issue_date"))
    when = dated or _utc_day(document.created_at)
    return start <= when <= end


def export(
    session_factory: sessionmaker[Session],
    settings: Settings,
    tenant_slug: str,
    start: date,
    end: date,
    folder: Path,
    *,
    now: datetime | None = None,
) -> Export:
    """Write the tenant's documents of the period to `folder` (new, or empty)."""
    if end < start:
        raise ValueError("the period ends before it begins")
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()):
        raise ValueError(f"{folder} is not empty: an export goes into a folder of its own")
    with session_factory() as session:
        tenant = session.scalar(select(TenantRow).where(TenantRow.slug == tenant_slug))
        if tenant is None:
            raise ValueError(f"no tenant {tenant_slug!r}")
        tenant_id, tenant_name = tenant.id, tenant.name
    (folder / "originals").mkdir(parents=True, exist_ok=True)
    result = Export(folder)
    documents, lines, history, deletions = [], [], [], []
    heads = hashlib.sha256()
    with tenant_transaction(session_factory, tenant_id) as session:
        config = config_for(session, tenant_id, settings)
        ids = list(
            session.scalars(
                select(DocumentRow.id)
                .where(DocumentRow.tenant_id == tenant_id)
                .order_by(DocumentRow.created_at, DocumentRow.id)
            )
        )
    for document_id in ids:
        with tenant_transaction(session_factory, tenant_id) as session:
            document = session.get(DocumentRow, document_id)
            if not in_period(document, start, end):
                continue
            found, _, _ = problems_of(document, tenant_slug)
            result.problems += [str(problem) for problem in found]
            content = document.content
            file = f"originals/{document.id}.{extension(content)}"
            (folder / file).write_bytes(content)
            documents.append(_document_row(document, file, end_of(document, config)))
            lines += _line_rows(document)
            history += _history_rows(document)
            heads.update(f"{document.id}:{document.audit_head}\n".encode())
    with tenant_transaction(session_factory, tenant_id) as session:
        for entry in session.scalars(
            select(DeletionLogRow)
            .where(DeletionLogRow.tenant_id == tenant_id)
            .order_by(DeletionLogRow.deleted_at)
        ):
            if start <= _utc_day(entry.deleted_at) <= end:
                deletions.append(
                    [
                        entry.document_id,
                        entry.sha256,
                        entry.audit_head or "",
                        str(entry.events),
                        _day(entry.received_at),
                        _day(entry.retention_end),
                        _day(entry.deleted_at),
                        entry.deleted_by,
                    ]
                )
    (folder / "belege.csv").write_bytes(_csv(DOCUMENTS, documents))
    (folder / "positionen.csv").write_bytes(_csv(LINES, lines))
    (folder / "protokoll.csv").write_bytes(_csv(HISTORY, history))
    (folder / "loeschungen.csv").write_bytes(_csv(DELETIONS, deletions))
    (folder / "index.xml").write_bytes(_index(tenant_name, start, end))
    result.documents, result.lines = len(documents), len(lines)
    result.events, result.deletions = len(history), len(deletions)
    result.digest = heads.hexdigest()
    made = (now or datetime.now(UTC)).astimezone(UTC)
    breaks = "\n".join(f"  {p}" for p in result.problems) or "  keine"
    (folder / "manifest.txt").write_text(
        f"""einvoice-agent: Datenträgerüberlassung
Mandant: {tenant_name} ({tenant_slug})
Zeitraum: {_day(start)} bis {_day(end)} (nach Belegdatum, sonst Eingangsdatum)
Erstellt: {made.strftime("%d.%m.%Y %H:%M:%S")} UTC
Belege: {result.documents}, Positionen: {result.lines}
Protokolleinträge: {result.events}, Löschungen: {result.deletions}
Prüfung der Originale (SHA-256) und der Hash-Ketten der Protokolle, Abweichungen:
{breaks}
Digest der Protokoll-Köpfe: {result.digest}
Prüfsummen aller Dateien: checksums.sha256 (shasum -a 256 -c checksums.sha256)
""",
        encoding="utf-8",
    )
    files = sorted(
        path for path in folder.rglob("*") if path.is_file() and path.name != "checksums.sha256"
    )
    sums = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(folder).as_posix()}\n"
        for path in files
    ]
    (folder / "checksums.sha256").write_text("".join(sums), encoding="utf-8")
    return result
