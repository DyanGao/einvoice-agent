"""Human review of a document: correct fields, then approve.

Every change is written to the audit trail with the old value, the new value
and who made it. The machine's original output is kept untouched next to the
corrected draft.
"""

from enum import StrEnum
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db.tables import AuditEventRow, DocumentRow, DraftRow, InvoiceRow, invoice_columns
from app.extract.draft import InvoiceDraft
from app.models import Invoice
from app.pipeline import DocumentStatus

# Not part of the invoice itself; reviewers cannot edit these.
READ_ONLY_FIELDS = {"source_format", "field_confidence", "derived_fields"}


class ReviewState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    # Set only by the decision engine (app/checks/service.py) when it routes a
    # document `auto`: nothing skips review without a decision on record.
    NOT_REQUIRED = "not_required"
    # A person decided it is not to be booked. Never delivered, checked again
    # or matched to payments, unless a person reopens it.
    REJECTED = "rejected"


class RejectReason(StrEnum):
    DUPLICATE = "duplicate"
    NOT_ADDRESSED_TO_US = "not_addressed_to_us"
    REPLACED_OR_CANCELLED = "replaced_or_cancelled"
    OTHER = "other"  # needs a note


class ReviewError(ValueError):
    """The requested review action is not possible; the message says why."""


class NotReviewable(ReviewError):
    """The document is not in a state that allows this action."""


def _container_and_key(data: dict[str, Any], path: str) -> tuple[Any, str | int]:
    parts = path.split(".")
    if not path or parts[0] in READ_ONLY_FIELDS:
        raise ReviewError(f"field cannot be edited: {path!r}")
    node: Any = data
    for part in parts[:-1]:
        node = _child(node, part, path)
    last = parts[-1]
    if isinstance(node, list):
        return node, _index(node, last, path)
    if not isinstance(node, dict) or last not in node:
        raise ReviewError(f"unknown field: {path!r}")
    return node, last


def _index(node: list, part: str, path: str) -> int:
    if not part.isdigit() or int(part) >= len(node):
        raise ReviewError(f"unknown field: {path!r}")
    return int(part)


def _child(node: Any, part: str, path: str) -> Any:
    if isinstance(node, list):
        return node[_index(node, part, path)]
    if not isinstance(node, dict) or part not in node:
        raise ReviewError(f"unknown field: {path!r}")
    return node[part]


def _require_pending(document: DocumentRow) -> None:
    if document.review_state == ReviewState.APPROVED.value:
        raise NotReviewable("document is already approved")
    if document.review_state == ReviewState.REJECTED.value:
        raise NotReviewable("document was rejected")
    if document.status == DocumentStatus.RECEIVED.value:
        raise NotReviewable("document has not been processed yet")


def correct_fields(
    session: Session,
    document: DocumentRow,
    changes: dict[str, Any],
    actor: str,
    actor_user_id: str | None = None,
) -> list[str]:
    """Apply field corrections given as {"dotted.path": value}. Returns the
    paths whose value actually changed. All or nothing: an invalid value or
    unknown path rejects the whole request."""
    _require_pending(document)
    if document.draft is None:
        # Nothing could be read: the reviewer starts from an empty invoice,
        # and the record shows that the machine produced nothing.
        empty = InvoiceDraft().model_dump(mode="json")
        document.draft = DraftRow(original=empty, data=empty)

    data = InvoiceDraft.model_validate(document.draft.data).model_dump(mode="json")
    before: dict[str, Any] = {}
    for path, value in changes.items():
        container, key = _container_and_key(data, path)
        before[path] = container[key]
        container[key] = value
    try:
        corrected = InvoiceDraft.model_validate(data).model_dump(mode="json")
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors()
        )
        raise ReviewError(f"invalid value: {problems}") from exc

    changed = []
    for path in changes:
        container, key = _container_and_key(corrected, path)
        if container[key] != before[path]:
            changed.append(path)
            document.audit_events.append(
                AuditEventRow(
                    event="field_corrected",
                    actor=actor,
                    actor_user_id=actor_user_id,
                    detail={"field": path, "old": before[path], "new": container[key]},
                )
            )
    document.draft.data = corrected
    session.flush()
    return changed


def missing_for_approval(draft: InvoiceDraft) -> list[str]:
    """Mandatory invoice fields the draft does not have yet."""
    try:
        draft.to_invoice()
    except ValidationError as exc:
        return sorted({".".join(map(str, error["loc"])) for error in exc.errors()})
    return []


def approve(
    session: Session, document: DocumentRow, actor: str, actor_user_id: str | None = None
) -> Invoice:
    """Turn the reviewed draft into the document's invoice of record."""
    _require_pending(document)
    if document.draft is None:
        raise NotReviewable("document has no draft to approve")
    draft = InvoiceDraft.model_validate(document.draft.data)
    missing = missing_for_approval(draft)
    if missing:
        raise ReviewError(f"cannot approve, fields missing or invalid: {', '.join(missing)}")
    invoice = draft.to_invoice()

    columns = invoice_columns(invoice)
    if document.invoice is None:
        document.invoice = InvoiceRow(**columns)
    else:
        for name, value in columns.items():
            setattr(document.invoice, name, value)
    document.review_state = ReviewState.APPROVED.value
    document.audit_events.append(
        AuditEventRow(
            event="approved",
            actor=actor,
            actor_user_id=actor_user_id,
            detail={"number": invoice.number},
        )
    )
    session.flush()
    return invoice


def reject(
    session: Session,
    document: DocumentRow,
    actor: str,
    reason: str,
    note: str | None = None,
    actor_user_id: str | None = None,
) -> None:
    """A person decides the document is not to be booked, and says why."""
    _require_pending(document)
    try:
        why = RejectReason(reason)
    except ValueError:
        allowed = ", ".join(r.value for r in RejectReason)
        raise ReviewError(f"unknown reason {reason!r}; one of: {allowed}") from None
    note = (note or "").strip() or None
    if why is RejectReason.OTHER and note is None:
        raise ReviewError("a rejection for another reason needs a note saying which")
    document.review_state = ReviewState.REJECTED.value
    document.audit_events.append(
        AuditEventRow(
            event="rejected",
            actor=actor,
            actor_user_id=actor_user_id,
            detail={"reason": why.value, "note": note},
        )
    )
    session.flush()


def reopen(
    session: Session,
    document: DocumentRow,
    actor: str,
    note: str,
    actor_user_id: str | None = None,
) -> None:
    """A rejection was a mistake: the document waits for review again. It is
    not checked again here; that, and approving it, are a person's next steps."""
    if document.review_state != ReviewState.REJECTED.value:
        raise NotReviewable("document is not rejected")
    note = (note or "").strip()
    if not note:
        raise ReviewError("reopening needs a note saying why")
    document.review_state = ReviewState.PENDING.value
    document.audit_events.append(
        AuditEventRow(
            event="reopened", actor=actor, actor_user_id=actor_user_id, detail={"note": note}
        )
    )
    session.flush()
