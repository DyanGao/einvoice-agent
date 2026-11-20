import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.review import ReviewState
from app.db.tables import (
    AuditEventRow,
    DocumentRow,
    DraftRow,
    InvoiceRow,
    TenantRow,
    ValidationRow,
    invoice_columns,
)
from app.models import DocumentFormat
from app.pipeline import DocumentStatus, Outcome

SYSTEM_ACTOR = "system"


def tenant_by_slug(session: Session, slug: str) -> TenantRow:
    """The tenant with this slug (as the CLI's --tenant names it)."""
    tenant = session.scalar(select(TenantRow).where(TenantRow.slug == slug))
    if tenant is None:
        raise LookupError(f"no tenant {slug!r}")
    return tenant


def tenant_of(session: Session, document_id: str) -> str | None:
    """Whose document this is. Background work asks this as the login role,
    before it switches to the tenant, so it is the one look-up across tenants."""
    return session.scalar(select(DocumentRow.tenant_id).where(DocumentRow.id == document_id))


def find_document(session: Session, data: bytes, tenant_id: str) -> DocumentRow | None:
    """The tenant's document with exactly these bytes, if it has one."""
    digest = hashlib.sha256(data).hexdigest()
    return session.scalar(
        select(DocumentRow).where(DocumentRow.tenant_id == tenant_id, DocumentRow.sha256 == digest)
    )


def record_duplicate(
    session: Session,
    document: DocumentRow,
    filename: str | None,
    source: dict[str, str] | None = None,
) -> None:
    """The same bytes arrived again: nothing is reprocessed, but it is noted."""
    detail: dict[str, object] = {"filename": filename}
    if source:
        detail["source"] = source
    document.audit_events.append(
        AuditEventRow(event="received_again", actor=SYSTEM_ACTOR, detail=detail)
    )


def record_queued_again(document: DocumentRow, waited_minutes: int) -> None:
    """A document that sat in "received" was handed to the queue again."""
    document.audit_events.append(_event("queued_again", waited_minutes=waited_minutes))


def create_received(
    session: Session,
    data: bytes,
    filename: str | None,
    tenant_id: str,
    source: dict[str, str] | None = None,
) -> DocumentRow:
    """Store an incoming document of a tenant before anything is known about
    it. `source` says where it came from (a mail's sender, subject,
    Message-ID): kept for the history, as data, never read as instructions."""
    document = DocumentRow(
        tenant_id=tenant_id,
        sha256=hashlib.sha256(data).hexdigest(),
        filename=filename,
        format=DocumentFormat.UNKNOWN.value,
        status=DocumentStatus.RECEIVED.value,
        review_state=ReviewState.PENDING.value,
        content=data,
    )
    detail: dict[str, object] = {"filename": filename, "bytes": len(data)}
    if source:
        detail["source"] = source
    document.audit_events.append(_event("received", **detail))
    session.add(document)
    session.flush()
    return document


def apply_outcome(session: Session, document: DocumentRow, outcome: Outcome) -> None:
    """Record what processing found out about a received document."""
    document.format = outcome.format.value
    document.status = outcome.status.value
    document.error = outcome.error
    # Waits for a person until the checks decide otherwise. Without checks
    # (no ERP configured) nothing decides, so a person has to look: showing
    # "no review needed" there would leave an invoice nothing ever books.
    document.review_state = ReviewState.PENDING.value
    document.audit_events.append(_event("format_detected", format=outcome.format.value))

    if outcome.validation is not None:
        validation = outcome.validation
        document.validation = ValidationRow(
            valid=validation.valid,
            scenario=validation.scenario,
            violations=[v.model_dump(mode="json") for v in validation.violations],
        )
        document.audit_events.append(
            _event(
                "validated",
                valid=validation.valid,
                scenario=validation.scenario,
                errors=[v.rule_id for v in validation.errors],
            )
        )

    if outcome.invoice is not None:
        document.invoice = InvoiceRow(**invoice_columns(outcome.invoice))
        document.audit_events.append(_event("parsed", number=outcome.invoice.number))

    if outcome.draft is not None:
        machine_output = outcome.draft.model_dump(mode="json")
        document.draft = DraftRow(original=machine_output, data=machine_output)
        if outcome.invoice is None:
            document.audit_events.append(_event("extracted", number=outcome.draft.number))

    document.audit_events.append(
        _event("status_set", status=outcome.status.value, error=outcome.error)
    )
    session.flush()


def store_outcome(
    session: Session, data: bytes, filename: str | None, outcome: Outcome, tenant_id: str
) -> DocumentRow:
    document = create_received(session, data, filename, tenant_id)
    apply_outcome(session, document, outcome)
    return document


def _event(event: str, **detail: object) -> AuditEventRow:
    return AuditEventRow(event=event, actor=SYSTEM_ACTOR, detail=detail)
