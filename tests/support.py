"""Shared builders for tests that need a processed document or a running app."""

from dataclasses import replace
from pathlib import Path

from app.erp import ErpUnavailable
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
    """Open mode by default, as the tests before sign-in were written; TestClient
    addresses its requests to "testserver"."""
    defaults = {"auth": "off", "open_hosts": ("localhost", "127.0.0.1", "::1", "testserver")}
    return replace(
        Settings.from_env(), feedback_dir=tmp_path / "feedback", **(defaults | overrides)
    )


def inline_queue(session_factory, validator=None, extractor=None, checker=None) -> InlineQueue:
    """A queue that processes at once, with a stub validator and the baseline
    extractor. Without a checker no business-rule checks run."""
    validator = validator or StubValidator(ACCEPT)
    extractor = extractor or BaselineExtractor()
    return InlineQueue(
        lambda document_id: run_document_job(
            session_factory, document_id, validator, extractor, checker
        )
    )


class FakeErp:
    """An ERP that knows nobody, or does not answer at all. Tests that need
    real master data use the mock ERP in memory (eval.match.erp_for)."""

    def __init__(self, down: bool = False) -> None:
        self.down = down

    def _answer(self) -> None:
        if self.down:
            raise ErpUnavailable("connection refused")

    def find_supplier(self, vat_id, name):
        self._answer()
        return None

    def orders_of(self, supplier_id):
        self._answer()
        return []

    def transactions_to(self, ibans):
        self._answer()
        return []


class RecordingQueue:
    """Remembers what was queued without processing it."""

    def __init__(self) -> None:
        self.document_ids: list[str] = []

    def enqueue(self, document_id: str) -> None:
        self.document_ids.append(document_id)


class BrokenQueue:
    def enqueue(self, document_id: str) -> None:
        raise QueueUnavailable("queue not reachable: connection refused")
