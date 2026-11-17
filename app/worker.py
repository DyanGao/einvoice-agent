"""The background worker:  python -m app.worker  (or `make worker`).

Besides running jobs, the worker sweeps for documents that sat in "received"
for longer than STUCK_AFTER without a job waiting or running for them (a
worker that died mid-job, a queue that was down at upload) and queues them
again, so no document waits for someone to run `einvoice requeue`.
"""

import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from functools import cache

import httpx
from redis import Redis
from rq import Queue, SimpleWorker
from sqlalchemy.orm import sessionmaker

from app.checks.service import build_checker
from app.db.engine import database_engine
from app.extract.factory import build_extractor
from app.ingest import requeue_stuck
from app.jobs import run_document_job
from app.queue import QUEUE_NAME, QueueUnavailable, RqQueue
from app.settings import Settings
from app.validate import KositValidator

logger = logging.getLogger(__name__)

SWEEP_SECONDS = 120
# Longer than a job normally waits and runs; a job still waiting or running
# is never queued twice, whatever its age.
STUCK_AFTER = timedelta(minutes=15)


def sweep_once(session_factory, queue: RqQueue, now: datetime) -> list[str]:
    queued = requeue_stuck(
        session_factory, queue, now=now, older_than=STUCK_AFTER, pending=queue.pending
    )
    if queued:
        logger.warning("queued %d stuck documents again: %s", len(queued), ", ".join(queued))
    return queued


def _sweep_forever(session_factory, queue: RqQueue) -> None:
    while True:
        try:
            sweep_once(session_factory, queue, datetime.now(UTC))
        except QueueUnavailable as exc:
            logger.warning("sweep for stuck documents: queue not reachable (%s)", exc)
        except Exception:
            # The sweep must not die silently and leave documents stuck again.
            logger.exception("sweep for stuck documents failed; trying again later")
        time.sleep(SWEEP_SECONDS)


@cache
def _context() -> tuple:
    settings = Settings.from_env()
    client = httpx.Client(
        base_url=settings.validator_url, timeout=settings.validator_timeout_seconds
    )
    return (
        sessionmaker(database_engine(settings.database_url)),
        KositValidator(client),
        build_extractor(settings),
        build_checker(settings.erp_url, settings.erp_timeout_seconds),
        settings.max_pages,
    )


def process_document_job(document_id: str) -> str:
    session_factory, validator, extractor, checker, max_pages = _context()
    return run_document_job(
        session_factory, document_id, validator, extractor, checker, max_pages=max_pages
    )


def main() -> None:
    """Run a worker until interrupted. SimpleWorker executes jobs in this
    process instead of forking, which also works on macOS."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = Settings.from_env()
    threading.Thread(
        target=_sweep_forever,
        args=(sessionmaker(database_engine(settings.database_url)), RqQueue(settings.redis_url)),
        name="stuck-document-sweep",
        daemon=True,
    ).start()
    connection = Redis.from_url(settings.redis_url)
    SimpleWorker([Queue(QUEUE_NAME, connection=connection)], connection=connection).work()


if __name__ == "__main__":
    main()
