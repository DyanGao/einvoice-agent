"""One entry point for any incoming document.

Structured e-invoices go to the validator and parser and never to an
extractor; PDFs and images go to the extractor; everything else is unsupported.
"""

import logging

from app.detect import detect
from app.extract.base import Extractor
from app.extract.llm import ExtractionError
from app.extract.ocr import OcrUnavailable
from app.models import DocumentFormat
from app.pipeline.structured import DocumentStatus, Outcome, Validator, process_structured

logger = logging.getLogger(__name__)

EXTRACTABLE = {DocumentFormat.PDF_TEXT, DocumentFormat.IMAGE}


def process_document(data: bytes, validator: Validator, extractor: Extractor | None) -> Outcome:
    fmt = detect(data)
    if fmt.is_structured:
        return process_structured(data, validator)
    if fmt not in EXTRACTABLE:
        return Outcome(format=fmt, status=DocumentStatus.UNSUPPORTED)
    if extractor is None:
        return Outcome(
            format=fmt, status=DocumentStatus.EXTRACTION_FAILED, error="no extractor configured"
        )
    try:
        draft = extractor.extract(data)
    except (ExtractionError, OcrUnavailable) as exc:
        logger.error("extraction failed: %s", exc)
        return Outcome(format=fmt, status=DocumentStatus.EXTRACTION_FAILED, error=str(exc))
    return Outcome(format=fmt, status=DocumentStatus.EXTRACTED, draft=draft)
