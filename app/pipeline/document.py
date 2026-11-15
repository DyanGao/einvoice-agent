"""One entry point for any incoming document.

Structured e-invoices go to the validator and parser and never to an
extractor; PDFs and images go to the extractor; everything else is unsupported.
A PDF with more pages than MAX_PAGES is not read at all: one model call for a
very long document costs money and minutes, and a person decides instead.
"""

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.detect import detect
from app.extract.base import Extractor
from app.extract.llm import ExtractionError
from app.extract.ocr import OcrUnavailable
from app.models import DocumentFormat
from app.pipeline.structured import DocumentStatus, Outcome, Validator, process_structured
from app.settings import DEFAULT_MAX_PAGES

logger = logging.getLogger(__name__)

EXTRACTABLE = {DocumentFormat.PDF_TEXT, DocumentFormat.IMAGE}
# Invoices are rarely longer; the MAX_PAGES setting changes it.
MAX_PAGES = DEFAULT_MAX_PAGES


def page_count(data: bytes) -> int | None:
    """Pages of a PDF, 1 for an image, None if a PDF cannot be read."""
    if not data.startswith(b"%PDF-"):
        return 1
    try:
        return len(PdfReader(io.BytesIO(data)).pages)
    except (PyPdfError, ValueError, KeyError, TypeError, OSError):
        return None


def process_document(
    data: bytes, validator: Validator, extractor: Extractor | None, max_pages: int = MAX_PAGES
) -> Outcome:
    fmt = detect(data)
    if fmt.is_structured:
        return process_structured(data, validator)
    if fmt not in EXTRACTABLE:
        return Outcome(format=fmt, status=DocumentStatus.UNSUPPORTED)
    if extractor is None:
        return Outcome(
            format=fmt, status=DocumentStatus.EXTRACTION_FAILED, error="no extractor configured"
        )
    pages = page_count(data)
    if pages is not None and pages > max_pages:
        return Outcome(
            format=fmt,
            status=DocumentStatus.EXTRACTION_FAILED,
            error=f"{pages} pages, more than the {max_pages} read automatically",
        )
    try:
        draft = extractor.extract(data)
    except (ExtractionError, OcrUnavailable) as exc:
        logger.error("extraction failed: %s", exc)
        return Outcome(format=fmt, status=DocumentStatus.EXTRACTION_FAILED, error=str(exc))
    return Outcome(format=fmt, status=DocumentStatus.EXTRACTED, draft=draft)
