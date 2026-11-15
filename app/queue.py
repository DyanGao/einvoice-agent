"""How documents get from "stored" to "processed"."""

from collections.abc import Callable
from typing import Protocol

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus

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


# A job in one of these states will still run; queuing its document again
# would process it twice (for a PDF: two paid model calls).
_WILL_RUN = {JobStatus.QUEUED, JobStatus.STARTED, JobStatus.DEFERRED, JobStatus.SCHEDULED}


def job_pending(connection: Redis, document_id: str) -> bool:
    """Whether the document's job is waiting or running. A finished, failed
    or stopped job (a worker that died mid-job) is not pending."""
    try:
        job = Job.fetch(RqQueue.job_id(document_id), connection=connection)
    except NoSuchJobError:
        return False
    return job.get_status() in _WILL_RUN


class RqQueue:
    def __init__(self, redis_url: str) -> None:
        self._queue = Queue(QUEUE_NAME, connection=Redis.from_url(redis_url))

    @staticmethod
    def job_id(document_id: str) -> str:
        """One job id per document, so a waiting job can be found again."""
        return f"document-{document_id}"

    def pending(self, document_id: str) -> bool:
        try:
            return job_pending(self._queue.connection, document_id)
        except RedisError as exc:
            raise QueueUnavailable(f"queue not reachable: {exc}") from exc

    def enqueue(self, document_id: str) -> None:
        try:
            if job_pending(self._queue.connection, document_id):
                return  # already waiting or running: once is enough
            self._queue.enqueue(
                JOB_FUNCTION,
                document_id,
                job_id=self.job_id(document_id),
                job_timeout=JOB_TIMEOUT_SECONDS,
            )
        except RedisError as exc:
            raise QueueUnavailable(f"queue not reachable: {exc}") from exc
