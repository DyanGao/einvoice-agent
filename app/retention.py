"""Retention and legal hold (v1.0 Stage 6, increment 5, O5).

How long: an invoice is kept `retention_years` (8, § 147 AO since 2025 — to
be confirmed with a tax adviser; never fewer) after the end of the latest
of these years: the one it is dated in, the one it was received in, and the
one it was approved or booked in. So it may go from 1 January of the year
after that; never before.

What keeps it longer: a legal hold on the document (`einvoice hold`), or on
the whole tenant (`legal_hold`, `einvoice tenant-set`).

What deletes it: nothing by itself. `einvoice retention` says what is due;
`einvoice retention --apply` deletes it, and only for a tenant whose
`deletion_enabled` is on. Each deletion leaves an entry in the deletion log:
the document's id, its original's SHA-256, its history's newest hash and
length, when it could go and when it went. The log is append-only.

    einvoice retention [--apply]
    einvoice hold DOCUMENT --tenant SLUG --reason TEXT [--release]
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.rls import DOCUMENT_TABLES, tenant_transaction
from app.db.tables import AuditEventRow, Base, CreditLinkRow, DeletionLogRow, DocumentRow, TenantRow
from app.settings import Settings
from app.tenants import TenantConfig, config_for

# The history events that say an invoice was taken into the books.
_BOOKED = frozenset({"approved", "delivered"})


def _year(value) -> int | None:
    if isinstance(value, datetime | date):
        return value.year
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10]).year
        except ValueError:
            return None
    return None


def retention_end(
    issued: date | None, received: datetime, booked: list[datetime], years: int
) -> date:
    """The last day the document must be kept."""
    if years < 8:
        raise ValueError("an invoice is kept at least 8 years")
    base = max(y for y in (_year(issued), received.year, *(b.year for b in booked)) if y)
    return date(base + years, 12, 31)


def may_go(end: date, today: date) -> bool:
    """From the day after its retention end; never before."""
    return today > end


def _issued(document: DocumentRow) -> date | None:
    """The date the invoice is from, as approved or as read; None if unread."""
    record = document.invoice if document.invoice is not None else document.draft
    value = None if record is None else record.data.get("issue_date")
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def end_of(document: DocumentRow, config: TenantConfig) -> date:
    booked = [e.created_at for e in document.audit_events if e.event in _BOOKED]
    return retention_end(_issued(document), document.created_at, booked, config.retention_years)


@dataclass
class TenantRetention:
    documents: int = 0
    kept: int = 0  # within their retention
    held: int = 0  # past it, but under a legal hold
    due: int = 0  # past it and not held
    deleted: int = 0
    # Past their end and not held, but the tenant's deletion is off: kept on purpose.
    deletion_off: bool = True
    next_end: date | None = None


@dataclass
class Report:
    tenants: dict[str, TenantRetention] = field(default_factory=dict)


def _delete(session: Session, document: DocumentRow, end: date, actor: str) -> None:
    session.add(
        DeletionLogRow(
            tenant_id=document.tenant_id,
            document_id=document.id,
            sha256=document.sha256,
            audit_head=document.audit_head,
            events=len(document.audit_events),
            received_at=document.created_at,
            retention_end=end,
            deleted_at=datetime.now(UTC),
            deleted_by=actor,
        )
    )
    document_id = document.id
    session.flush()
    session.expunge_all()  # everything of it goes below, not through the ORM
    tables = Base.metadata.tables
    # A credit note that corrects it keeps its own life: only the link goes.
    links = CreditLinkRow.__table__
    session.execute(
        update(links)
        .where(links.c.invoice_document_id == document_id)
        .values(invoice_document_id=None)
    )
    for name in DOCUMENT_TABLES:
        table = tables[name]
        session.execute(delete(table).where(table.c.document_id == document_id))
    documents = DocumentRow.__table__
    session.execute(delete(documents).where(documents.c.id == document_id))


def run(
    session_factory: sessionmaker[Session],
    settings: Settings,
    today: date,
    *,
    apply: bool,
    actor: str = "retention",
) -> Report:
    """What is due in every tenant; with `apply`, deleted where the tenant's
    deletion is on."""
    report = Report()
    with session_factory() as session:
        tenants = list(
            session.execute(select(TenantRow.id, TenantRow.slug).order_by(TenantRow.slug))
        )
    for tenant_id, slug in tenants:
        with tenant_transaction(session_factory, tenant_id) as session:
            config = config_for(session, tenant_id, settings)
            ids = list(
                session.scalars(select(DocumentRow.id).where(DocumentRow.tenant_id == tenant_id))
            )
        found = TenantRetention(deletion_off=not config.deletion_enabled)
        for document_id in ids:
            with tenant_transaction(session_factory, tenant_id) as session:
                document = session.get(DocumentRow, document_id)
                end = end_of(document, config)
                found.documents += 1
                if not may_go(end, today):
                    found.kept += 1
                    found.next_end = min(filter(None, (found.next_end, end)))
                    continue
                if config.legal_hold or document.legal_hold:
                    found.held += 1
                    continue
                found.due += 1
                if apply and config.deletion_enabled:
                    _delete(session, document, end, actor)
                    found.deleted += 1
        report.tenants[slug] = found
    return report


def set_hold(session: Session, document: DocumentRow, *, on: bool, reason: str, actor: str) -> None:
    """Put a document under a legal hold, or take it off; why goes into its
    history (and so into its chain)."""
    if not reason.strip():
        raise ValueError("a legal hold needs a reason")
    if document.legal_hold == on:
        raise ValueError("the document is under a legal hold already" if on else "no legal hold")
    document.legal_hold = on
    document.audit_events.append(
        AuditEventRow(
            event="legal_hold" if on else "legal_hold_released",
            actor=actor,
            detail={"reason": reason.strip()},
        )
    )
