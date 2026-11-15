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


# --- page limit (hardening H4): a long document is not sent to the model ---


def pages_of(source: bytes, count: int) -> bytes:
    """A PDF made of `count` copies of the first page of `source`."""
    import io

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    page = PdfReader(io.BytesIO(source)).pages[0]
    for _ in range(count):
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def scanned_pages(count: int) -> bytes:
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(595, 842)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_pdf_longer_than_the_limit_goes_to_a_person_unread(accepting):
    extractor = SpyExtractor()
    outcome = process_document(pages_of(PDF, 11), accepting, extractor, max_pages=10)
    assert (outcome.status, extractor.seen) == (DocumentStatus.EXTRACTION_FAILED, [])
    assert outcome.error == "11 pages, more than the 10 read automatically"


def test_pdf_at_the_limit_is_read(accepting):
    extractor = SpyExtractor()
    process_document(pages_of(PDF, 10), accepting, extractor, max_pages=10)
    assert len(extractor.seen) == 1


def test_scanned_pdf_longer_than_the_limit_is_not_read_either(accepting):
    extractor = SpyExtractor()
    outcome = process_document(scanned_pages(11), accepting, extractor, max_pages=10)
    assert (outcome.status, extractor.seen) == (DocumentStatus.EXTRACTION_FAILED, [])


def test_an_image_is_one_page(accepting):
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (60, 80), "white").save(out, format="JPEG")
    extractor = SpyExtractor()
    process_document(out.getvalue(), accepting, extractor, max_pages=1)
    assert len(extractor.seen) == 1


def test_pages_of_an_unreadable_pdf_are_unknown():
    from app.pipeline.document import page_count

    assert page_count(b"%PDF-1.7\n%not really a pdf\n") is None


def test_pdf_whose_pages_cannot_be_counted_is_left_to_the_extractor(accepting, monkeypatch):
    """Not counting is not a reason to refuse: the extractor fails on its own terms."""
    monkeypatch.setattr("app.pipeline.document.page_count", lambda data: None)
    extractor = SpyExtractor()
    process_document(PDF, accepting, extractor, max_pages=1)
    assert extractor.seen == [PDF]


def test_default_limit_reads_an_ordinary_invoice(accepting):
    extractor = SpyExtractor()
    process_document(PDF, accepting, extractor)
    assert len(extractor.seen) == 1
