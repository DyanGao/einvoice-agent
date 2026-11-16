from collections.abc import Callable

import anthropic

from app.extract.base import Extractor
from app.extract.baseline import BaselineExtractor
from app.extract.complete import CompletingExtractor
from app.extract.confidence import Calibration, LastDocumentText, ScoringExtractor
from app.extract.draft import InvoiceDraft
from app.extract.llm import AnthropicLlm, ExtractionError
from app.extract.llm_extractor import LlmExtractor
from app.settings import Settings


class LazyExtractor:
    """Builds the real extractor on first use.

    A worker that only ever sees structured invoices must not need model
    credentials, and missing credentials must fail one document, not the worker.
    """

    def __init__(self, build: Callable[[], Extractor]) -> None:
        self._build = build
        self._extractor: Extractor | None = None

    def extract(self, data: bytes) -> InvoiceDraft:
        if self._extractor is None:
            try:
                self._extractor = self._build()
            except anthropic.AnthropicError as exc:
                raise ExtractionError(f"model client is not configured: {exc}") from exc
        return self._extractor.extract(data)


def _llm_extractor(settings: Settings) -> Extractor:
    """Read, fill in what follows by rule, then say how sure each field is.
    Both later steps look at the document's own text; OCR runs once."""
    text_of = LastDocumentText()
    return ScoringExtractor(
        CompletingExtractor(
            LlmExtractor(
                AnthropicLlm(anthropic.Anthropic()),
                model=settings.extractor_model,
                effort=settings.extractor_effort,
            ),
            text_of=text_of,
        ),
        Calibration.load(),
        text_of=text_of,
    )


def build_extractor(settings: Settings) -> Extractor | None:
    if settings.extractor == "none":
        return None
    if settings.extractor == "baseline":
        return BaselineExtractor()
    if settings.extractor == "llm":
        return LazyExtractor(lambda: _llm_extractor(settings))
    raise ValueError(f"EXTRACTOR must be llm, baseline or none, not {settings.extractor!r}")
