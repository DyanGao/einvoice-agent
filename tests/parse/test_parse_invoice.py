from decimal import Decimal

import pytest

from app.models import DocumentFormat
from app.parse import InvoiceParseError, parse_invoice
from tests.detect.pdf_factory import make_pdf
from tests.parse import samples


def test_two_payment_accounts_are_both_kept():
    assert len(parse_invoice(samples.ubl("01.05a")).payment_ibans) == 2


def test_missing_payment_account_gives_empty_tuple():
    assert parse_invoice(samples.cii("01.19a")).payment_ibans == ()


def test_purchase_order_reference_is_read():
    assert parse_invoice(samples.cii("01.14a")).po_ref == "65002278"


def test_vat_id_written_with_a_space_is_normalized():
    assert b"DE 123456789" in samples.ubl("01.01a")
    assert parse_invoice(samples.ubl("01.01a")).seller.vat_id == "DE123456789"


def test_gross_price_is_not_mistaken_for_net_price():
    assert b"<ram:ChargeAmount>1010.00</ram:ChargeAmount>" in samples.cii("03.06a")
    assert parse_invoice(samples.cii("03.06a")).lines[0].unit_price == Decimal("1000.00")


def test_totals_are_read_from_the_document_not_recomputed():
    xml = samples.ubl("01.01a").replace(b">336.9</cbc:PayableAmount>", b">1.00</cbc:PayableAmount>")
    assert parse_invoice(xml).amount_due == Decimal("1.00")


def test_ubl_na_order_reference_next_to_sales_order_means_no_po():
    xml = samples.ubl("01.02a").replace(b"<cbc:ID>Dummywert</cbc:ID>", b"<cbc:ID>NA</cbc:ID>")
    assert parse_invoice(xml).po_ref is None


def test_ubl_credit_note_is_parsed_with_its_own_element_names():
    xml = (
        samples.ubl("01.01a")
        .replace(b"ubl:Invoice", b"ubl:CreditNote")
        .replace(b"xsd:Invoice-2", b"xsd:CreditNote-2")
        .replace(b"InvoiceTypeCode>380<", b"CreditNoteTypeCode>381<")
        .replace(b"InvoiceTypeCode", b"CreditNoteTypeCode")
        .replace(b"cac:InvoiceLine", b"cac:CreditNoteLine")
        .replace(b"cbc:InvoicedQuantity", b"cbc:CreditedQuantity")
    )
    invoice = parse_invoice(xml)
    assert (invoice.is_credit_note, len(invoice.lines)) == (True, 2)


def test_zugferd_pdf_is_parsed_from_its_embedded_xml():
    pdf = make_pdf(text="Rechnung 123456XX", attachments={"factur-x.xml": samples.cii("01.01a")})
    invoice = parse_invoice(pdf)
    assert (invoice.number, invoice.source_format) == ("123456XX", DocumentFormat.ZUGFERD_PDF)


@pytest.mark.parametrize(
    ("removed", "expected_message"),
    [
        (b"<cbc:ID>123456XX</cbc:ID>", "BT-1"),
        (b"<cbc:IssueDate>2016-04-04</cbc:IssueDate>", "BT-2"),
        (b"<cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>", "BT-5"),
        (b"<cbc:RegistrationName>[Seller name]</cbc:RegistrationName>", "BT-27"),
        (b'<cbc:PayableAmount currencyID="EUR">336.9</cbc:PayableAmount>', "BT-115"),
    ],
    ids=["number", "issue-date", "currency", "seller-name", "amount-due"],
)
def test_missing_mandatory_field_names_the_business_term(removed, expected_message):
    xml = samples.ubl("01.01a")
    assert removed in xml
    with pytest.raises(InvoiceParseError, match=expected_message):
        parse_invoice(xml.replace(removed, b""))


def test_non_numeric_amount_is_rejected():
    xml = samples.ubl("01.01a").replace(b">336.9</cbc:PayableAmount>", b">viel</cbc:PayableAmount>")
    with pytest.raises(InvoiceParseError, match="BT-115"):
        parse_invoice(xml)


@pytest.mark.parametrize("value", [b"NaN", b"Infinity"])
def test_non_finite_amount_is_rejected(value):
    xml = samples.ubl("01.01a").replace(
        b">336.9</cbc:PayableAmount>", b">%s</cbc:PayableAmount>" % value
    )
    with pytest.raises(InvoiceParseError, match="BT-115"):
        parse_invoice(xml)


def test_impossible_date_is_rejected():
    xml = samples.cii("01.01a").replace(b">20160404<", b">20161340<")
    with pytest.raises(InvoiceParseError, match="BT-2"):
        parse_invoice(xml)


def test_cii_without_transaction_is_rejected():
    xml = (
        b"<rsm:CrossIndustryInvoice"
        b' xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"/>'
    )
    with pytest.raises(InvoiceParseError, match="ExchangedDocument"):
        parse_invoice(xml)


@pytest.mark.parametrize(
    "data",
    [b"", b"hello", make_pdf(text="Rechnung Nr. 1 Gesamtbetrag 119,00 EUR"), b"\x89PNG\r\n\x1a\n"],
    ids=["empty", "text", "plain-pdf", "image"],
)
def test_unstructured_input_is_not_parsed(data):
    with pytest.raises(InvoiceParseError, match="not a structured e-invoice"):
        parse_invoice(data)
