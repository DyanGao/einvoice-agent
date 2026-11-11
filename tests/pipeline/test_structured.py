from app.models import DocumentFormat
from app.pipeline import DocumentStatus, process_structured
from tests.conftest import ACCEPT, NO_SCENARIO, StubValidator
from tests.detect.pdf_factory import make_pdf
from tests.parse import samples


def test_accepted_and_parsed_document_is_valid(accepting):
    outcome = process_structured(samples.ubl("01.01a"), accepting)
    assert (outcome.status, outcome.invoice.number) == (DocumentStatus.VALID, "123456XX")


def test_rejected_document_is_invalid_but_still_parsed(rejecting):
    outcome = process_structured(samples.ubl("01.01a"), rejecting)
    assert (outcome.status, outcome.invoice is not None) == (DocumentStatus.INVALID, True)


def test_validator_failure_is_never_valid():
    outcome = process_structured(samples.ubl("01.01a"), StubValidator(error="connection refused"))
    assert outcome.status is DocumentStatus.VALIDATION_FAILED


def test_validator_failure_is_reported_in_error():
    outcome = process_structured(samples.ubl("01.01a"), StubValidator(error="connection refused"))
    assert "connection refused" in outcome.error


def test_document_without_matching_scenario_is_unrecognized():
    outcome = process_structured(samples.cii("01.01a"), StubValidator(NO_SCENARIO))
    assert outcome.status is DocumentStatus.UNRECOGNIZED


def test_accepted_document_we_cannot_read_is_parse_failed(accepting):
    broken = samples.ubl("01.01a").replace(b"<cbc:ID>123456XX</cbc:ID>", b"")
    outcome = process_structured(broken, accepting)
    assert (outcome.status, outcome.invoice) == (DocumentStatus.PARSE_FAILED, None)


def test_rejected_document_we_cannot_read_is_invalid(rejecting):
    broken = samples.ubl("01.01a").replace(b"<cbc:ID>123456XX</cbc:ID>", b"")
    assert process_structured(broken, rejecting).status is DocumentStatus.INVALID


def test_plain_pdf_is_unsupported_and_not_sent_to_the_validator(accepting):
    outcome = process_structured(make_pdf(text="Rechnung Nr. 1 Gesamtbetrag 119,00 EUR"), accepting)
    assert (outcome.status, outcome.format, accepting.seen) == (
        DocumentStatus.UNSUPPORTED,
        DocumentFormat.PDF_TEXT,
        [],
    )


def test_zugferd_pdf_sends_its_embedded_xml_to_the_validator():
    validator = StubValidator(ACCEPT)
    xml = samples.cii("01.01a")
    process_structured(make_pdf(text="Rechnung", attachments={"factur-x.xml": xml}), validator)
    assert validator.seen == [xml]
