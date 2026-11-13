"""How documents get from "stored" to "processed"."""

from collections.abc import Callable
from typing import Protocol

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue

QUEUE_NAME = "einvoice"
JOB_FUNCTION = "app.worker.process_document_job"
JOB_TIMEOUT_SECONDS = 600


class QueueUnavailable(RuntimeError):
    """The job could not be handed over; the document stays `received`."""


class JobQueue(Protocol):
    def enqueue(self, document_id: str) -> None: ...


class InlineQueue:
    """Processes the document immediately in the caller. For tests and for
    running without Redis; not for production, where uploads would block."""

    def __init__(self, run: Callable[[str], object]) -> None:
        self._run = run

    def enqueue(self, document_id: str) -> None:
        self._run(document_id)


class RqQueue:
    def __init__(self, redis_url: str) -> None:
        self._queue = Queue(QUEUE_NAME, connection=Redis.from_url(redis_url))

    def enqueue(self, document_id: str) -> None:
        try:
            self._queue.enqueue(JOB_FUNCTION, document_id, job_timeout=JOB_TIMEOUT_SECONDS)
        except RedisError as exc:
            raise QueueUnavailable(f"queue not reachable: {exc}") from exc
