"""Invoice data sealed at rest (v1.0 Stage 6, increment 2): every column that
holds what an invoice says is stored sealed with its tenant's key, rows from
before are read and sealed by `einvoice seal`, and a dump of a database with a
processed, matched and booked invoice shows none of its values."""

import sqlite3
from datetime import UTC, datetime

import pytest
from sqlalchemy import LargeBinary, create_engine, literal, select, type_coerce, update
from sqlalchemy.orm import sessionmaker

from app import crypto, db
from app.checks.service import Checker
from app.db.rls import tenant_transaction
from app.delivery import booking_for
from app.ingest import ingest
from app.sealing import seal_documents, sealed_columns
from eval.generators.canonical import XRECHNUNG
from eval.generators.world import build_world
from eval.generators.xrechnung import render_ubl
from eval.match import erp_for
from tests.support import inline_queue

WORLD = build_world()
# An invoice with an order, a delivery and a payment in the world's ERP.
ENTRY = next(e for e in WORLD.invoices if e.invoice.po_ref and e.payments)
INVOICE = ENTRY.invoice
MAIL_SENDER, MAIL_SUBJECT = "buchhaltung@lieferant-7c2e.example", "Rechnung zu Auftrag 7c2e"


def known_values() -> list[str]:
    """What the invoice says, as a reader of the dump would look for it."""
    return [
        INVOICE.number,
        INVOICE.seller.name,
        INVOICE.seller.vat_id,
        INVOICE.buyer.name,
        INVOICE.ibans[0],
        INVOICE.items[0].name,
        INVOICE.po_ref,
        str(INVOICE.gross_total),
        MAIL_SENDER,
        MAIL_SUBJECT,
    ]


def processed(session_factory, tenant_id: str = db.DEFAULT_TENANT_ID) -> str:
    """Read, checked against the ERP, matched, decided, a booking stored, and
    the mail it came in."""
    queue = inline_queue(session_factory, checker=Checker(erp_for(WORLD)))
    document_id = ingest(
        session_factory,
        queue,
        render_ubl(INVOICE, XRECHNUNG),
        f"{INVOICE.number}.xml",
        tenant_id=tenant_id,
    ).document_id
    with tenant_transaction(session_factory, tenant_id) as session:
        document = session.get(db.DocumentRow, document_id)
        if tenant_id != db.DEFAULT_TENANT_ID:  # the ERP in these tests is the default's
            return document_id
        document.deliveries.append(
            db.DeliveryRow(
                connector="csv_export",
                idempotency_key=f"key-{document_id}",
                status="failed",
                booking=booking_for(document).as_json(),
                attempts=1,
                next_attempt_at=datetime.now(UTC),
                last_error=f"could not book {INVOICE.number}",
            )
        )
        session.add(
            db.MailRow(
                tenant_id=tenant_id,
                mailbox="INBOX",
                uidvalidity=1,
                uid=1,
                message_id="<m1@example>",
                sender=MAIL_SENDER,
                subject=MAIL_SUBJECT,
                sent="Fri, 20 Nov 2026 10:00:00 +0100",
                parts=[{"filename": f"{INVOICE.number}.xml", "document_id": document_id}],
            )
        )
    return document_id


def raw_values(session_factory):
    """Every value of every sealed column, as stored."""
    with session_factory() as session:
        for table, name, _ in sealed_columns():
            raw = type_coerce(table.c[name], LargeBinary)
            for (value,) in session.execute(select(raw).where(table.c[name].is_not(None))):
                yield f"{table.name}.{name}", value


def test_every_column_of_invoice_data_is_stored_sealed(session_factory):
    document_id = processed(session_factory)
    stored = list(raw_values(session_factory))
    assert [place for place, value in stored if not crypto.is_sealed(value)] == []
    # The places an invoice's data goes to were all written.
    assert {place for place, _ in stored} >= {
        "documents.filename",
        "drafts.original",
        "drafts.data",
        "invoices.data",
        "validation_results.violations",
        "audit_events.detail",
        "matches.po_number",
        "matches.payments",
        "matches.explanation",
        "decisions.reasons",
        "deliveries.booking",
        "deliveries.last_error",
        "mails.sender",
        "mails.subject",
        "mails.parts",
    }
    with session_factory() as session:
        document = session.get(db.DocumentRow, document_id)
        assert document.draft.data["number"] == INVOICE.number
        assert document.match.po_number is not None
        assert document.deliveries[0].booking["number"] == INVOICE.number


def test_findings_are_stored_sealed(session_factory):
    from tests.support import PDF, FakeErp

    queue = inline_queue(session_factory, checker=Checker(FakeErp()))
    document_id = ingest(session_factory, queue, PDF, "r.pdf").document_id
    findings = db.FindingRow.__table__
    with session_factory() as session:
        raw = session.execute(
            select(
                type_coerce(findings.c.message, LargeBinary),
                type_coerce(findings.c.detail, LargeBinary),
            ).where(findings.c.document_id == document_id)
        ).all()
        messages = [f.message for f in session.get(db.DocumentRow, document_id).findings]
    assert raw
    assert all(crypto.is_sealed(message) and crypto.is_sealed(detail) for message, detail in raw)
    assert all(messages)


def test_another_tenants_rows_are_sealed_with_its_own_key(session_factory):
    with session_factory.begin() as session:
        session.add(db.TenantRow(id="b", slug="b", name="B"))
    processed(session_factory)
    processed(session_factory, tenant_id="b")
    key_of = slice(len(crypto.MAGIC), len(crypto.MAGIC) + 16)
    with session_factory() as session:
        keys = {
            tenant: session.scalar(
                select(type_coerce(db.DraftRow.data, LargeBinary))
                .join(db.DocumentRow)
                .where(db.DocumentRow.tenant_id == tenant)
            )[key_of]
            for tenant in (db.DEFAULT_TENANT_ID, "b")
        }
    assert keys[db.DEFAULT_TENANT_ID] != keys["b"]


def _as_before_sealing(session_factory, document_id: str) -> None:
    """The draft, a finding and the file name as the migration leaves them:
    the plain bytes of their JSON and text."""
    with session_factory.begin() as session:
        document = session.get(db.DocumentRow, document_id)
        data, name = document.draft.data, document.filename
        draft = db.DraftRow.__table__
        session.execute(
            update(draft)
            .where(draft.c.document_id == document_id)
            .values(data=literal(db.DraftRow.data.type.plain_bytes(data), LargeBinary))
        )
        documents = db.DocumentRow.__table__
        session.execute(
            update(documents)
            .where(documents.c.id == document_id)
            .values(filename=literal(name.encode(), LargeBinary))
        )


def test_values_stored_before_sealing_are_read_and_then_sealed(session_factory):
    document_id = processed(session_factory)
    _as_before_sealing(session_factory, document_id)
    with session_factory() as session:
        document = session.get(db.DocumentRow, document_id)
        assert (document.draft.data["number"], document.filename) == (
            INVOICE.number,
            f"{INVOICE.number}.xml",
        )
    report = seal_documents(session_factory, apply=True)
    assert +report.by_place == {"drafts.data": 1, "documents.filename": 1}
    assert all(crypto.is_sealed(value) for _, value in raw_values(session_factory))
    with session_factory() as session:
        assert session.get(db.DocumentRow, document_id).draft.data["number"] == INVOICE.number


def test_open_stores_the_invoice_data_plain_again_and_it_still_reads(session_factory):
    document_id = processed(session_factory)
    seal_documents(session_factory, apply=True, open_all=True)
    assert not any(crypto.is_sealed(value) for _, value in raw_values(session_factory))
    with session_factory() as session:
        assert session.get(db.DocumentRow, document_id).draft.data["number"] == INVOICE.number


def _dump(path) -> str:
    connection = sqlite3.connect(path)
    try:
        return "\n".join(connection.iterdump())
    finally:
        connection.close()


def _shown(value: str, dump: str) -> bool:
    """As text, or as the hex of a BLOB holding it."""
    return value in dump or value.encode().hex().upper() in dump


@pytest.mark.parametrize("encryption", ["on", "off"])
def test_a_dump_of_a_processed_invoice_shows_none_of_its_values(tmp_path, encryption):
    path = tmp_path / "einvoice.db"
    engine = create_engine(f"sqlite:///{path}")
    db.Base.metadata.create_all(engine)
    if encryption == "off":
        crypto.configure(None)  # the control: then the same dump shows them
    processed(sessionmaker(engine))
    engine.dispose()
    dump = _dump(path)
    shown = [value for value in known_values() if _shown(value, dump)]
    assert shown == ([] if encryption == "on" else known_values())
