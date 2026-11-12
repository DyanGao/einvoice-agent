import json
import os

from app.cli import main
from app.ingest import SETTLE_SECONDS
from tests.support import XML, BrokenQueue, RecordingQueue


def settled(path):
    path.write_bytes(XML)
    old = path.stat().st_mtime - 10 * SETTLE_SECONDS
    os.utime(path, (old, old))


def test_watch_once_queues_the_files_in_the_folder(capsys, tmp_path, session_factory):
    settled(tmp_path / "rechnung.xml")
    queue = RecordingQueue()
    code = main(["watch", str(tmp_path), "--once"], session_factory=session_factory, queue=queue)
    (line,) = capsys.readouterr().out.splitlines()
    assert (code, json.loads(line)["document_id"]) == (0, queue.document_ids[0])


def test_watch_on_something_that_is_not_a_folder_fails(capsys, tmp_path, session_factory):
    code = main(
        ["watch", str(tmp_path / "missing"), "--once"],
        session_factory=session_factory,
        queue=RecordingQueue(),
    )
    assert (code, "not a folder" in capsys.readouterr().err) == (2, True)


def test_requeue_queues_documents_left_behind_by_a_queue_outage(capsys, tmp_path, session_factory):
    import pytest

    from app.queue import QueueUnavailable

    settled(tmp_path / "rechnung.xml")
    with pytest.raises(QueueUnavailable):
        main(
            ["watch", str(tmp_path), "--once"], session_factory=session_factory, queue=BrokenQueue()
        )
    queue = RecordingQueue()
    main(["requeue"], session_factory=session_factory, queue=queue)
    assert (capsys.readouterr().out.strip(), len(queue.document_ids)) == ("queued 1 documents", 1)
