import os

import pytest
from sqlalchemy import func, select

from app import db
from app.ingest import SETTLE_SECONDS, ingest, requeue_received, scan_folder
from app.jobs import run_document_job
from app.queue import QueueUnavailable
from tests.conftest import ACCEPT, StubValidator
from tests.support import PDF, XML, BrokenQueue, RecordingQueue, inline_queue

MAX_BYTES = 1_000_000


def count(session_factory) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(db.DocumentRow))


def events(session_factory, document_id) -> list[str]:
    with session_factory() as session:
        return [e.event for e in session.get(db.DocumentRow, document_id).audit_events]


def settled(path, data=XML):
    """A file whose last change is old enough to be picked up."""
    path.write_bytes(data)
    old = path.stat().st_mtime - 10 * SETTLE_SECONDS
    os.utime(path, (old, old))
    return path


def test_new_document_is_stored_as_received_and_queued(session_factory):
    queue = RecordingQueue()
    result = ingest(session_factory, queue, XML, "rechnung.xml")
    assert (result.status, result.duplicate, queue.document_ids) == (
        "received",
        False,
        [result.document_id],
    )


def test_document_is_committed_before_it_is_queued(session_factory):
    # The inline queue processes inside enqueue(); that only works if the row
    # is already visible to another session.
    result = ingest(session_factory, inline_queue(session_factory), XML, "rechnung.xml")
    with session_factory() as session:
        assert session.get(db.DocumentRow, result.document_id).status == "valid"


def test_same_bytes_again_are_not_stored_or_queued_twice(session_factory):
    queue = RecordingQueue()
    first = ingest(session_factory, queue, XML, "a.xml")
    second = ingest(session_factory, queue, XML, "b.xml")
    assert (second.document_id, second.duplicate, len(queue.document_ids)) == (
        first.document_id,
        True,
        1,
    )
    assert events(session_factory, first.document_id)[-1] == "received_again"


def test_directory_parts_of_a_filename_are_dropped(session_factory):
    result = ingest(session_factory, RecordingQueue(), XML, "../../etc/passwd")
    with session_factory() as session:
        assert session.get(db.DocumentRow, result.document_id).filename == "passwd"


def test_document_survives_a_queue_outage_and_is_picked_up_by_requeue(session_factory):
    with pytest.raises(QueueUnavailable):
        ingest(session_factory, BrokenQueue(), XML, "rechnung.xml")
    queue = RecordingQueue()
    assert (count(session_factory), requeue_received(session_factory, queue)) == (1, 1)
    assert len(queue.document_ids) == 1


def test_requeue_ignores_documents_that_were_already_processed(session_factory):
    ingest(session_factory, inline_queue(session_factory), XML, "rechnung.xml")
    assert requeue_received(session_factory, RecordingQueue()) == 0


def test_job_run_twice_changes_nothing_the_second_time(session_factory):
    queue = RecordingQueue()
    document_id = ingest(session_factory, queue, PDF, "rechnung.pdf").document_id
    validator = StubValidator(ACCEPT)
    first = run_document_job(session_factory, document_id, validator, None)
    trail = events(session_factory, document_id)
    second = run_document_job(session_factory, document_id, validator, None)
    assert (first, second, events(session_factory, document_id)) == (
        "extraction_failed",
        "extraction_failed",
        trail,
    )


def test_job_for_an_unknown_document_is_an_error(session_factory):
    with pytest.raises(LookupError):
        run_document_job(session_factory, "missing", StubValidator(ACCEPT), None)


def test_folder_scan_ingests_a_settled_file(session_factory, tmp_path):
    settled(tmp_path / "rechnung.xml")
    queue = RecordingQueue()
    (result,) = scan_folder(tmp_path, session_factory, queue, MAX_BYTES)
    assert queue.document_ids == [result.document_id]


def test_folder_scan_leaves_the_file_in_place(session_factory, tmp_path):
    path = settled(tmp_path / "rechnung.xml")
    scan_folder(tmp_path, session_factory, RecordingQueue(), MAX_BYTES)
    assert path.read_bytes() == XML


def test_second_scan_neither_ingests_again_nor_touches_the_audit_trail(session_factory, tmp_path):
    settled(tmp_path / "rechnung.xml")
    queue = RecordingQueue()
    (first,) = scan_folder(tmp_path, session_factory, queue, MAX_BYTES)
    trail = events(session_factory, first.document_id)
    assert scan_folder(tmp_path, session_factory, queue, MAX_BYTES) == []
    assert (len(queue.document_ids), events(session_factory, first.document_id)) == (1, trail)


def test_file_still_being_written_is_left_for_the_next_scan(session_factory, tmp_path):
    (tmp_path / "rechnung.xml").write_bytes(XML)  # modified just now
    assert scan_folder(tmp_path, session_factory, RecordingQueue(), MAX_BYTES) == []


def test_hidden_empty_oversized_and_non_files_are_skipped(session_factory, tmp_path):
    settled(tmp_path / ".hidden.xml")
    settled(tmp_path / "empty.xml", b"")
    settled(tmp_path / "huge.xml", b"x" * 2000)
    (tmp_path / "subfolder").mkdir()
    outside = settled(tmp_path / "subfolder" / "nested.xml")
    (tmp_path / "link.xml").symlink_to(outside)
    assert scan_folder(tmp_path, session_factory, RecordingQueue(), max_bytes=1000) == []
    assert count(session_factory) == 0
