import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.review import ReviewState
from app.db.tables import (
    AuditEventRow,
    DocumentRow,
    DraftRow,
    InvoiceRow,
    ValidationRow,
    invoice_columns,
)
from app.models import DocumentFormat
from app.pipeline import DocumentStatus, Outcome

SYSTEM_ACTOR = "system"


def find_document(session: Session, data: bytes) -> DocumentRow | None:
    digest = hashlib.sha256(data).hexdigest()
    return session.scalar(select(DocumentRow).where(DocumentRow.sha256 == digest))


def record_duplicate(session: Session, document: DocumentRow, filename: str | None) -> None:
    """The same bytes arrived again: nothing is reprocessed, but it is noted."""
    document.audit_events.append(
        AuditEventRow(event="received_again", actor=SYSTEM_ACTOR, detail={"filename": filename})
    )


def record_queued_again(document: DocumentRow, waited_minutes: int) -> None:
    """A document that sat in "received" was handed to the queue again."""
    document.audit_events.append(_event("queued_again", waited_minutes=waited_minutes))


def create_received(session: Session, data: bytes, filename: str | None) -> DocumentRow:
    """Store an incoming document before anything is known about it."""
    document = DocumentRow(
        sha256=hashlib.sha256(data).hexdigest(),
        filename=filename,
        format=DocumentFormat.UNKNOWN.value,
        status=DocumentStatus.RECEIVED.value,
        review_state=ReviewState.PENDING.value,
        content=data,
    )
    document.audit_events.append(_event("received", filename=filename, bytes=len(data)))
    session.add(document)
    session.flush()
    return document


def apply_outcome(session: Session, document: DocumentRow, outcome: Outcome) -> None:
    """Record what processing found out about a received document."""
    document.format = outcome.format.value
    document.status = outcome.status.value
    document.error = outcome.error
    document.review_state = ReviewState.after_processing(outcome.status).value
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
    session: Session, data: bytes, filename: str | None, outcome: Outcome
) -> DocumentRow:
    document = create_received(session, data, filename)
    apply_outcome(session, document, outcome)
    return document


def _event(event: str, **detail: object) -> AuditEventRow:
    return AuditEventRow(event=event, actor=SYSTEM_ACTOR, detail=detail)
