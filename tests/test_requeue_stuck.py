"""Documents left in "received" are queued again without a person (hardening H3)."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from redis import Redis

from app import db
from app.ingest import requeue_stuck
from app.queue import QueueUnavailable, RqQueue, job_pending
from tests.support import PDF, XML, BrokenQueue, RecordingQueue, inline_queue

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
LIMIT = timedelta(minutes=10)


def received(session_factory, data=XML, minutes_ago=30) -> str:
    """A document stored and never processed, `minutes_ago` before NOW."""
    with session_factory.begin() as session:
        document = db.create_received(session, data, "rechnung.xml")
        document.created_at = NOW - timedelta(minutes=minutes_ago)
        session.flush()
        return document.id


def nothing_pending(document_id: str) -> bool:
    return False


def sweep(session_factory, queue, pending=nothing_pending):
    return requeue_stuck(session_factory, queue, now=NOW, older_than=LIMIT, pending=pending)


def test_document_received_long_ago_is_queued_again(session_factory):
    document_id = received(session_factory)
    queue = RecordingQueue()
    assert sweep(session_factory, queue) == [document_id]
    assert queue.document_ids == [document_id]


def test_queuing_again_is_in_the_audit_trail(session_factory):
    document_id = received(session_factory)
    sweep(session_factory, RecordingQueue())
    with session_factory() as session:
        last = session.get(db.DocumentRow, document_id).audit_events[-1]
    assert (last.event, last.actor) == ("queued_again", "system")


def test_document_received_just_now_is_left_to_the_queue(session_factory):
    received(session_factory, minutes_ago=2)
    queue = RecordingQueue()
    assert (sweep(session_factory, queue), queue.document_ids) == ([], [])


def test_document_whose_job_is_still_waiting_is_not_queued_twice(session_factory):
    """Two jobs for one PDF would be two paid model calls."""
    received(session_factory)
    queue = RecordingQueue()
    assert sweep(session_factory, queue, pending=lambda document_id: True) == []


def test_processed_document_is_not_queued_again(session_factory):
    from app.ingest import ingest

    ingest(session_factory, inline_queue(session_factory), PDF, "rechnung.pdf")
    assert sweep(session_factory, RecordingQueue()) == []


def test_queue_down_is_reported_and_the_document_stays_received(session_factory):
    document_id = received(session_factory)
    with pytest.raises(QueueUnavailable):
        sweep(session_factory, BrokenQueue())
    with session_factory() as session:
        assert session.get(db.DocumentRow, document_id).status == "received"


# --- the real queue: needs Redis (make test-pg sets TEST_REDIS_URL) ----------

on_redis = pytest.mark.skipif(
    not os.environ.get("TEST_REDIS_URL"), reason="needs Redis (make test-pg)"
)


@pytest.fixture
def redis_url():
    url = os.environ["TEST_REDIS_URL"]
    connection = Redis.from_url(url)
    connection.flushdb()
    yield url
    connection.flushdb()


@on_redis
def test_queued_job_is_pending(redis_url):
    RqQueue(redis_url).enqueue("doc-1")
    assert job_pending(Redis.from_url(redis_url), "doc-1")


@on_redis
def test_document_without_a_job_is_not_pending(redis_url):
    assert not job_pending(Redis.from_url(redis_url), "doc-never-queued")


@on_redis
def test_failed_job_is_not_pending(redis_url):
    """A worker that died mid-job leaves it failed: that one must run again."""
    from rq.job import Job

    RqQueue(redis_url).enqueue("doc-1")
    connection = Redis.from_url(redis_url)
    job = Job.fetch(RqQueue.job_id("doc-1"), connection=connection)
    job.set_status("failed")
    assert not job_pending(connection, "doc-1")


@on_redis
def test_queuing_a_document_twice_leaves_one_job_waiting(redis_url):
    queue = RqQueue(redis_url)
    queue.enqueue("doc-1")
    queue.enqueue("doc-1")
    connection = Redis.from_url(redis_url)
    from rq import Queue

    from app.queue import QUEUE_NAME

    assert Queue(QUEUE_NAME, connection=connection).count == 1


class PendingQueue(RecordingQueue):
    def __init__(self, waiting: set[str]) -> None:
        super().__init__()
        self.waiting = waiting

    def pending(self, document_id: str) -> bool:
        return document_id in self.waiting


def test_worker_sweep_skips_documents_whose_job_is_waiting(session_factory):
    from app.worker import STUCK_AFTER, sweep_once

    stuck = received(session_factory, data=XML, minutes_ago=STUCK_AFTER.seconds // 60 + 5)
    waiting = received(session_factory, data=PDF, minutes_ago=STUCK_AFTER.seconds // 60 + 5)
    queue = PendingQueue({waiting})
    assert sweep_once(session_factory, queue, NOW) == [stuck]
