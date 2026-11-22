"""A hash chain over each document's history (v1.0 Stage 6, increment 4).

Every audit event stores `hash` = SHA-256 of the hash before it and of what
the event says (document, event, actor, verified user, detail, when). The
first event's "hash before" is the original's SHA-256, so the chain starts at
the file as received. The document keeps the newest hash (`audit_head`), so
a history cut short at its end is noticed too.

The hash covers what the event says, not how it is stored: sealing it again
under a new key (app/rotation.py) leaves the chain as it is.

Chained right before the session writes, in the order the events were made;
the head is read again from the database with a row lock, so two
transactions adding to one history cannot fork it. Events stored before the
chain existed have no hash: `einvoice chain --apply` chains them
(app/verify.py), and `einvoice verify` checks every chain and original.
"""

import hashlib
import itertools
import json
from datetime import UTC, datetime

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.db.tables import AuditEventRow, DocumentRow, _new_id

_made = itertools.count()


@event.listens_for(AuditEventRow, "init")
def _number(target: AuditEventRow, args, kwargs) -> None:
    # The order events were made in, which is the order of the history.
    target._made = next(_made)


def instant(moment: datetime) -> str:
    """One way to write a moment, whatever the database gave back (SQLite
    returns naive datetimes for what was stored as UTC)."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def event_hash(before: str, document_id: str, row: AuditEventRow) -> str:
    said = json.dumps(
        {
            "document": document_id,
            "event": row.event,
            "actor": row.actor,
            "actor_user_id": row.actor_user_id,
            "detail": row.detail,
            "at": instant(row.created_at),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"{before}\n{said}".encode()).hexdigest()


def _head(session: Session, document: DocumentRow) -> str:
    """The newest hash of the document's history as stored, locked until the
    transaction ends; the original's SHA-256 for a history not chained yet."""
    stored = session.execute(
        select(DocumentRow.audit_head).where(DocumentRow.id == document.id).with_for_update()
    ).first()
    head = stored[0] if stored is not None else document.audit_head
    return head or document.sha256


@event.listens_for(Session, "before_flush")
def _chain(session: Session, flush_context, instances) -> None:
    new = [row for row in session.new if isinstance(row, AuditEventRow)]
    if not new:
        return
    # By the document object: a new document has no id until here.
    by_document: dict[int, tuple[DocumentRow, list[AuditEventRow]]] = {}
    for row in new:
        document = (
            row.document if row.document is not None else session.get(DocumentRow, row.document_id)
        )
        if document is None:
            # Not this tenant's, or no such document: the insert itself is
            # refused (row-level security, the foreign key).
            continue
        by_document.setdefault(id(document), (document, []))[1].append(row)
    for document, rows in by_document.values():
        if document.id is None:  # made here, as it would be on insert
            document.id = _new_id()
        with session.no_autoflush:
            before = _head(session, document)
        for row in sorted(rows, key=lambda r: getattr(r, "_made", 0)):
            if row.created_at is None:
                row.created_at = datetime.now(UTC)
            row.prev_hash = before
            row.hash = event_hash(before, document.id, row)
            before = row.hash
        document.audit_head = before
