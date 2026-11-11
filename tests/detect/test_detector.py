import pytest

from app.detect import detect, extract_embedded_invoice_xml
from app.models import DocumentFormat
from tests.detect.pdf_factory import make_pdf

UBL_INVOICE = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>'
)
UBL_CREDIT_NOTE = b'<CreditNote xmlns="urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"/>'
CII_INVOICE = (
    b"<rsm:CrossIndustryInvoice"
    b' xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"/>'
)
INVOICE_TEXT = "Rechnung Nr. 2026-001 Gesamtbetrag 119,00 EUR"


def test_ubl_invoice_is_detected():
    assert detect(UBL_INVOICE) is DocumentFormat.UBL


def test_ubl_credit_note_is_detected():
    assert detect(UBL_CREDIT_NOTE) is DocumentFormat.UBL


def test_cii_invoice_is_detected():
    assert detect(CII_INVOICE) is DocumentFormat.CII


def test_xml_with_leading_bom_is_detected():
    assert detect(b"\xef\xbb\xbf" + UBL_INVOICE) is DocumentFormat.UBL


def test_pdf_with_embedded_factur_x_is_zugferd():
    pdf = make_pdf(text=INVOICE_TEXT, attachments={"factur-x.xml": CII_INVOICE})
    assert detect(pdf) is DocumentFormat.ZUGFERD_PDF


def test_legacy_zugferd_attachment_name_is_matched_case_insensitively():
    pdf = make_pdf(text=INVOICE_TEXT, attachments={"ZUGFeRD-invoice.xml": CII_INVOICE})
    assert detect(pdf) is DocumentFormat.ZUGFERD_PDF


def test_embedded_invoice_xml_is_returned_unchanged():
    pdf = make_pdf(text=INVOICE_TEXT, attachments={"factur-x.xml": CII_INVOICE})
    assert extract_embedded_invoice_xml(pdf) == CII_INVOICE


def test_pdf_with_text_and_no_attachment_is_text_pdf():
    assert detect(make_pdf(text=INVOICE_TEXT)) is DocumentFormat.PDF_TEXT


def test_pdf_without_text_is_treated_as_scan():
    assert detect(make_pdf(text=None)) is DocumentFormat.IMAGE


def test_pdf_with_unrelated_xml_attachment_is_not_zugferd():
    pdf = make_pdf(text=INVOICE_TEXT, attachments={"notes.xml": CII_INVOICE})
    assert detect(pdf) is DocumentFormat.PDF_TEXT


def test_pdf_whose_invoice_attachment_is_not_an_invoice_is_not_zugferd():
    pdf = make_pdf(text=INVOICE_TEXT, attachments={"factur-x.xml": b"<html/>"})
    assert detect(pdf) is DocumentFormat.PDF_TEXT


@pytest.mark.parametrize(
    "header",
    [b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff\xe0", b"II*\x00", b"MM\x00*"],
    ids=["png", "jpeg", "tiff-le", "tiff-be"],
)
def test_image_signatures_are_detected(header):
    assert detect(header + b"\x00" * 16) is DocumentFormat.IMAGE


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"just some text",
        b'<Invoice xmlns="urn:example:not-an-invoice"/>',
        b"<Invoice/>",
        UBL_INVOICE[:-10],
        b"%PDF-1.7\ngarbage",
    ],
    ids=["empty", "plain-text", "foreign-namespace", "no-namespace", "truncated-xml", "broken-pdf"],
)
def test_unclassifiable_input_is_unknown(data):
    assert detect(data) is DocumentFormat.UNKNOWN


def test_xml_declaring_entities_is_refused():
    bomb = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE Invoice [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">&b;</Invoice>'
    )
    assert detect(bomb) is DocumentFormat.UNKNOWN


def test_xml_with_external_entity_is_refused():
    xxe = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE Invoice [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">&x;</Invoice>'
    )
    assert detect(xxe) is DocumentFormat.UNKNOWN
