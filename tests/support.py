"""Shared builders for tests that need a processed document or a running app."""

from dataclasses import replace
from pathlib import Path

from app.extract import BaselineExtractor
from app.jobs import run_document_job
from app.queue import InlineQueue, QueueUnavailable
from app.settings import Settings
from eval.generators.pdf import generate as generate_pdfs
from tests.conftest import ACCEPT, StubValidator
from tests.parse import samples

XML = samples.ubl("01.01a")
# A generated text PDF in a dev layout: the rule-based extractor reads it completely.
PDF_DOCUMENT = next(generate_pdfs(1))
PDF = PDF_DOCUMENT.content


def settings_for(tmp_path: Path, **overrides) -> Settings:
    return replace(Settings.from_env(), feedback_dir=tmp_path / "feedback", **overrides)


def inline_queue(session_factory, validator=None, extractor=None) -> InlineQueue:
    """A queue that processes at once, with a stub validator and the baseline extractor."""
    validator = validator or StubValidator(ACCEPT)
    extractor = extractor or BaselineExtractor()
    return InlineQueue(
        lambda document_id: run_document_job(session_factory, document_id, validator, extractor)
    )


class RecordingQueue:
    """Remembers what was queued without processing it."""

    def __init__(self) -> None:
        self.document_ids: list[str] = []

    def enqueue(self, document_id: str) -> None:
        self.document_ids.append(document_id)


class BrokenQueue:
    def enqueue(self, document_id: str) -> None:
        raise QueueUnavailable("queue not reachable: connection refused")
