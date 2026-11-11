"""The structured path: detect → validate → parse. No LLM, no I/O besides the
validator call, so the whole step is a function of (bytes, validator)."""

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.detect import detect, extract_embedded_invoice_xml
from app.models import DocumentFormat, Invoice
from app.parse import InvoiceParseError, parse_invoice
from app.validate import ValidationResult, ValidatorError

logger = logging.getLogger(__name__)


class DocumentStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    # The validator has no scenario for this document type (e.g. a ZUGFeRD
    # profile outside EN 16931). Neither confirmed nor refuted.
    UNRECOGNIZED = "unrecognized"
    # The validator accepted it but we could not read it: a bug on our side.
    PARSE_FAILED = "parse_failed"
    # No verdict could be obtained. Must never be treated as valid.
    VALIDATION_FAILED = "validation_failed"
    # Not a structured e-invoice; belongs to the extraction path.
    UNSUPPORTED = "unsupported"


class Validator(Protocol):
    def validate(self, xml: bytes) -> ValidationResult: ...


@dataclass(frozen=True)
class Outcome:
    format: DocumentFormat
    status: DocumentStatus
    invoice: Invoice | None = None
    validation: ValidationResult | None = None
    error: str | None = None


def process_structured(data: bytes, validator: Validator) -> Outcome:
    fmt = detect(data)
    if not fmt.is_structured:
        return Outcome(format=fmt, status=DocumentStatus.UNSUPPORTED)

    xml = extract_embedded_invoice_xml(data) if fmt is DocumentFormat.ZUGFERD_PDF else data
    errors: list[str] = []

    validation = None
    try:
        validation = validator.validate(xml)
    except ValidatorError as exc:
        logger.error("validation failed: %s", exc)
        errors.append(str(exc))

    invoice = None
    try:
        invoice = parse_invoice(data)
    except InvoiceParseError as exc:
        logger.warning("parse failed: %s", exc)
        errors.append(str(exc))

    return Outcome(
        format=fmt,
        status=_status(validation, invoice),
        invoice=invoice,
        validation=validation,
        error="; ".join(errors) or None,
    )


def _status(validation: ValidationResult | None, invoice: Invoice | None) -> DocumentStatus:
    if validation is None:
        return DocumentStatus.VALIDATION_FAILED
    if validation.scenario is None:
        return DocumentStatus.UNRECOGNIZED
    if not validation.valid:
        return DocumentStatus.INVALID
    if invoice is None:
        return DocumentStatus.PARSE_FAILED
    return DocumentStatus.VALID
