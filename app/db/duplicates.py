"""Copies of an invoice that a person may reject together.

A supplier that sends the same invoice twice, in another file, leaves a copy
in the review queue whose only problem is that it is a copy. Those can be
rejected in one go, but only where nothing else is in doubt:

- it waits for review only because it is a duplicate (same supplier, same
  number): no other finding and no other reason, such as an unsure field;
- the invoice it copies was received earlier and is not rejected, so the
  earliest copy always stays (the duplicate check itself only compares with
  earlier documents; this keeps it so for findings recorded before that);
- both have the same gross total: the same number with another total may be
  a corrected invoice, which a person has to look at.

Near-duplicates (similar number, same amount) are never selected.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.review import RejectReason, ReviewState, reject
from app.db.tables import DocumentRow

DUPLICATE = "duplicate"


@dataclass(frozen=True)
class RedundantCopy:
    document: DocumentRow
    original: DocumentRow


@dataclass
class BulkRejection:
    rejected: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _gross(document: DocumentRow) -> Decimal | None:
    if document.draft is None:
        return None
    try:
        value = document.draft.data.get("gross_total")
        return None if value is None else Decimal(str(value))
    except InvalidOperation:
        return None


def _only_duplicate(document: DocumentRow) -> bool:
    if not document.findings or any(f.type != DUPLICATE for f in document.findings):
        return False
    decision = document.decision
    if decision is None or decision.route != "review":
        return False
    return all(
        reason.get("cause") == "findings" and set(reason.get("findings") or []) == {DUPLICATE}
        for reason in decision.reasons
    )


def _earlier(original: DocumentRow, document: DocumentRow) -> bool:
    return (original.created_at, original.id) < (document.created_at, document.id)


def copy_of(session: Session, document: DocumentRow) -> DocumentRow | None:
    """The earlier, not rejected invoice this document is a plain copy of."""
    if document.review_state != ReviewState.PENDING.value or not _only_duplicate(document):
        return None
    gross = _gross(document)
    if gross is None:
        return None
    for finding in document.findings:
        original = session.get(DocumentRow, finding.detail.get("document_id"))
        if (
            original is not None
            and original.review_state != ReviewState.REJECTED.value
            and _earlier(original, document)
            and _gross(original) == gross
        ):
            return original
    return None


def redundant_copies(session: Session) -> list[RedundantCopy]:
    pending = session.scalars(
        select(DocumentRow)
        .where(DocumentRow.review_state == ReviewState.PENDING.value)
        .order_by(DocumentRow.created_at, DocumentRow.id)
    )
    copies = []
    for document in pending:
        original = copy_of(session, document)
        if original is not None:
            copies.append(RedundantCopy(document, original))
    return copies


def reject_copies(
    session: Session, document_ids: list[str], actor: str, actor_user_id: str | None = None
) -> BulkRejection:
    """Reject the given documents that are still redundant copies; the rest
    are skipped. Each is checked again here, so a list shown earlier cannot
    reject a document that has changed since."""
    result = BulkRejection()
    for document_id in document_ids:
        document = session.get(DocumentRow, document_id)
        original = None if document is None else copy_of(session, document)
        if original is None:
            result.skipped.append(document_id)
            continue
        number = original.draft.data.get("number") if original.draft else None
        reject(
            session,
            document,
            actor,
            RejectReason.DUPLICATE.value,
            f"copy of invoice {number or '(no number)'}, document {original.id}",
            actor_user_id=actor_user_id,
        )
        result.rejected.append(document_id)
    return result
