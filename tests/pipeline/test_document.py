import pytest

from app.extract import ExtractionError, InvoiceDraft
from app.extract.ocr import OcrUnavailable
from app.models import DocumentFormat
from app.pipeline import DocumentStatus, process_document
from tests.detect.pdf_factory import make_pdf
from tests.support import PDF, XML


class SpyExtractor:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result or InvoiceDraft(number="RE-1", source_format=DocumentFormat.PDF_TEXT)
        self.error = error
        self.seen: list[bytes] = []

    def extract(self, data: bytes) -> InvoiceDraft:
        self.seen.append(data)
        if self.error:
            raise self.error
        return self.result


def test_structured_invoice_is_validated_and_never_reaches_the_extractor(accepting):
    extractor = SpyExtractor()
    outcome = process_document(XML, accepting, extractor)
    assert (outcome.status, extractor.seen, len(accepting.seen)) == (DocumentStatus.VALID, [], 1)


def test_zugferd_pdf_never_reaches_the_extractor(accepting):
    from tests.parse import samples

    extractor = SpyExtractor()
    pdf = make_pdf(text="Rechnung", attachments={"factur-x.xml": samples.cii("01.01a")})
    process_document(pdf, accepting, extractor)
    assert extractor.seen == []


def test_parsed_structured_invoice_is_also_available_as_a_draft(accepting):
    outcome = process_document(XML, accepting, None)
    assert outcome.draft.to_invoice() == outcome.invoice


def test_text_pdf_is_extracted_and_never_reaches_the_validator(accepting):
    extractor = SpyExtractor()
    outcome = process_document(PDF, accepting, extractor)
    assert (outcome.status, outcome.draft.number, accepting.seen) == (
        DocumentStatus.EXTRACTED,
        "RE-1",
        [],
    )


def test_extracted_document_has_no_invoice_of_record_yet(accepting):
    assert process_document(PDF, accepting, SpyExtractor()).invoice is None


@pytest.mark.parametrize(
    "error",
    [ExtractionError("model stopped with 'refusal'"), OcrUnavailable("language data missing")],
    ids=["model", "ocr"],
)
def test_extractor_failure_is_recorded_not_raised_and_nothing_is_guessed(accepting, error):
    outcome = process_document(PDF, accepting, SpyExtractor(error=error))
    assert (outcome.status, outcome.draft, outcome.error) == (
        DocumentStatus.EXTRACTION_FAILED,
        None,
        str(error),
    )


def test_pdf_without_a_configured_extractor_fails_with_that_reason(accepting):
    outcome = process_document(PDF, accepting, None)
    assert (outcome.status, outcome.error) == (
        DocumentStatus.EXTRACTION_FAILED,
        "no extractor configured",
    )


@pytest.mark.parametrize("data", [b"hello", b"<html/>"], ids=["text", "foreign-xml"])
def test_other_input_is_unsupported_and_touches_neither_service(accepting, data):
    extractor = SpyExtractor()
    outcome = process_document(data, accepting, extractor)
    assert (outcome.status, extractor.seen, accepting.seen) == (DocumentStatus.UNSUPPORTED, [], [])
