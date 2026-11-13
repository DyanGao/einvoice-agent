"""Getting documents into the system: store first, then hand to the queue."""

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import db
from app.pipeline import DocumentStatus
from app.queue import JobQueue

logger = logging.getLogger(__name__)

# A file still being copied into the folder keeps changing; leave it for the next round.
SETTLE_SECONDS = 2.0


@dataclass(frozen=True)
class Ingested:
    document_id: str
    status: str
    duplicate: bool


def ingest(
    session_factory: sessionmaker[Session],
    queue: JobQueue,
    data: bytes,
    filename: str | None,
    *,
    note_duplicate: bool = True,
) -> Ingested:
    """Store the document and queue it. The same bytes are never stored twice.

    The job is queued only after the row is committed, so a worker can never
    look for a document that does not exist yet. If queueing fails the
    document stays `received` and `requeue_received` picks it up later.
    """
    name = Path(filename).name[:255] if filename else None
    with session_factory.begin() as session:
        existing = db.find_document(session, data)
        if existing is not None:
            if note_duplicate:
                db.record_duplicate(session, existing, name)
            return Ingested(existing.id, existing.status, duplicate=True)
        document_id = db.create_received(session, data, name).id
    queue.enqueue(document_id)
    return Ingested(document_id, DocumentStatus.RECEIVED.value, duplicate=False)


def requeue_received(session_factory: sessionmaker[Session], queue: JobQueue) -> int:
    """Queue every document that was stored but never processed."""
    with session_factory() as session:
        ids = session.scalars(
            select(db.DocumentRow.id).where(db.DocumentRow.status == DocumentStatus.RECEIVED.value)
        ).all()
    for document_id in ids:
        queue.enqueue(document_id)
    return len(ids)


def scan_folder(
    folder: Path,
    session_factory: sessionmaker[Session],
    queue: JobQueue,
    max_bytes: int,
    now: float | None = None,
) -> list[Ingested]:
    """Ingest every settled, regular, non-hidden file directly in `folder`.

    Files are left where they are; a file already known by content is skipped
    without touching its audit trail, so polling does not flood it.
    """
    now = time.time() if now is None else now
    new = []
    for path in sorted(folder.iterdir()):
        if path.name.startswith(".") or not path.is_file() or path.is_symlink():
            continue
        stat = path.stat()
        if stat.st_size == 0 or now - stat.st_mtime < SETTLE_SECONDS:
            continue
        if stat.st_size > max_bytes:
            logger.warning("skipping %s: larger than %d bytes", path.name, max_bytes)
            continue
        result = ingest(session_factory, queue, path.read_bytes(), path.name, note_duplicate=False)
        if not result.duplicate:
            new.append(result)
    return new
