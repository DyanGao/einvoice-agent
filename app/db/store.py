import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import AuditEventRow, DocumentRow, InvoiceRow, ValidationRow
from app.pipeline import Outcome

SYSTEM_ACTOR = "system"


def find_document(session: Session, data: bytes) -> DocumentRow | None:
    digest = hashlib.sha256(data).hexdigest()
    return session.scalar(select(DocumentRow).where(DocumentRow.sha256 == digest))


def record_duplicate(session: Session, document: DocumentRow, filename: str | None) -> None:
    """The same bytes arrived again: nothing is reprocessed, but it is noted."""
    document.audit_events.append(
        AuditEventRow(event="received_again", actor=SYSTEM_ACTOR, detail={"filename": filename})
    )


def store_outcome(
    session: Session, data: bytes, filename: str | None, outcome: Outcome
) -> DocumentRow:
    document = DocumentRow(
        sha256=hashlib.sha256(data).hexdigest(),
        filename=filename,
        format=outcome.format.value,
        status=outcome.status.value,
        error=outcome.error,
        content=data,
    )
    document.audit_events.append(_event("received", filename=filename, bytes=len(data)))
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
        invoice = outcome.invoice
        document.invoice = InvoiceRow(
            number=invoice.number,
            issue_date=invoice.issue_date,
            seller_name=invoice.seller.name,
            seller_vat_id=invoice.seller.vat_id,
            currency=invoice.currency,
            gross_total=invoice.gross_total,
            data=invoice.model_dump(mode="json"),
        )
        document.audit_events.append(_event("parsed", number=invoice.number))

    document.audit_events.append(
        _event("status_set", status=outcome.status.value, error=outcome.error)
    )
    session.add(document)
    session.flush()
    return document


def _event(event: str, **detail: object) -> AuditEventRow:
    return AuditEventRow(event=event, actor=SYSTEM_ACTOR, detail=detail)
