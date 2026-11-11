"""Real ZUGFeRD / Factur-X PDFs produced by third-party software.

Expected values were read from the embedded XML by eye or with a plain text
search, not with this project's parsers.
"""

from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.detect import detect
from app.models import DocumentFormat, Invoice, Line, Party, VatBreakdown
from app.parse import InvoiceParseError, parse_invoice
from app.pipeline import DocumentStatus, process_structured
from tests.conftest import NO_SCENARIO, StubValidator

CORPUS = Path(__file__).parents[1] / "fixtures" / "zugferd-corpus"

EN16931_FILES = ["EN16931_Einfach.pdf", "EN16931_Rabatte.pdf", "EN16931_Gutschrift.pdf"]
OTHER_PROFILE_FILES = [
    "XRECHNUNG_Einfach.pdf",
    "zugferd_2p0_EXTENDED_Warenrechnung.pdf",
    "zugferd_2p0_MINIMUM.pdf",
]


def sample(name: str) -> bytes:
    return (CORPUS / name).read_bytes()


GOLD_EINFACH = Invoice(
    number="471102",
    issue_date=date(2018, 3, 5),
    type_code="380",
    currency="EUR",
    seller=Party(name="Lieferant GmbH", vat_id="DE123456789"),
    buyer=Party(name="Kunden AG Mitte"),
    lines=(
        Line(
            line_id="1",
            description="Trennblätter A4",
            quantity=D("20"),
            unit_price=D("9.90"),
            net=D("198.00"),
            vat_rate=D("19"),
        ),
        Line(
            line_id="2",
            description="Joghurt Banane",
            quantity=D("50"),
            unit_price=D("5.50"),
            net=D("275.00"),
            vat_rate=D("7"),
        ),
    ),
    vat_breakdown=(
        VatBreakdown(category="S", rate=D("7"), taxable_amount=D("275.00"), tax_amount=D("19.25")),
        VatBreakdown(category="S", rate=D("19"), taxable_amount=D("198.00"), tax_amount=D("37.62")),
    ),
    line_net_total=D("473.00"),
    net_total=D("473.00"),
    vat_total=D("56.87"),
    gross_total=D("529.87"),
    amount_due=D("529.87"),
    source_format=DocumentFormat.ZUGFERD_PDF,
)


@pytest.mark.parametrize("name", EN16931_FILES + OTHER_PROFILE_FILES)
def test_zugferd_2_pdf_is_detected_whatever_its_attachment_is_called(name):
    assert detect(sample(name)) is DocumentFormat.ZUGFERD_PDF


def test_simple_sample_equals_hand_written_gold():
    assert parse_invoice(sample("EN16931_Einfach.pdf")) == GOLD_EINFACH


def test_totals_with_allowances_charges_and_prepayment_are_read_as_stated():
    invoice = parse_invoice(sample("EN16931_Rabatte.pdf"))
    assert (
        invoice.line_net_total,
        invoice.net_total,
        invoice.vat_total,
        invoice.gross_total,
        invoice.amount_due,
    ) == (D("202.70"), D("193.77"), D("21.30"), D("215.07"), D("165.07"))


def test_purchase_order_reference_is_read_from_a_real_file():
    assert parse_invoice(sample("EN16931_Rabatte.pdf")).po_ref == "2013-471331"


def test_german_gutschrift_is_self_billing_not_a_credit_note():
    invoice = parse_invoice(sample("EN16931_Gutschrift.pdf"))
    assert (invoice.type_code, invoice.is_credit_note) == ("389", False)


def test_xrechnung_attachment_in_a_pdf_is_parsed():
    invoice = parse_invoice(sample("XRECHNUNG_Einfach.pdf"))
    assert (invoice.number, invoice.payment_ibans) == ("471102", ("DE02120300000000202051",))


def test_extended_profile_is_parsed():
    invoice = parse_invoice(sample("zugferd_2p0_EXTENDED_Warenrechnung.pdf"))
    assert (invoice.number, len(invoice.lines), invoice.gross_total) == (
        "R87654321012345",
        6,
        D("518.99"),
    )


def test_minimum_profile_is_a_booking_aid_without_lines():
    invoice = parse_invoice(sample("zugferd_2p0_MINIMUM.pdf"))
    assert (invoice.type_code, invoice.lines) == ("751", ())


def test_profile_unknown_to_the_validator_is_unrecognized_but_keeps_the_parsed_invoice():
    outcome = process_structured(
        sample("zugferd_2p0_EXTENDED_Warenrechnung.pdf"), StubValidator(NO_SCENARIO)
    )
    assert (outcome.status, outcome.invoice.number) == (
        DocumentStatus.UNRECOGNIZED,
        "R87654321012345",
    )


def test_zugferd_1_is_handled_as_an_ordinary_pdf():
    # ZUGFeRD 1.0 predates EN 16931 and uses another XML schema. It does not
    # count as an e-invoice under the 2025 rules, so it takes the extraction
    # path like any other PDF instead of the structured one.
    data = sample("ZUGFeRD_1p0_BASIC_Einfach.pdf")
    assert detect(data) is DocumentFormat.PDF_TEXT
    with pytest.raises(InvoiceParseError, match="not a structured e-invoice"):
        parse_invoice(data)
