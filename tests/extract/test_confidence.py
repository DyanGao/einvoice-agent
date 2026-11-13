from datetime import date
from decimal import Decimal

import pytest

from app.extract import InvoiceDraft, LineDraft, PartyDraft, VatBreakdownDraft
from app.extract.confidence import (
    ASSUMED_CONFIDENCE,
    Calibration,
    Check,
    Ground,
    ScoringExtractor,
    assess,
    bucket,
    score,
)
from app.models import DocumentFormat

IBAN = "DE89370400440532013000"  # the standard example IBAN, checksum valid


def draft(**fields) -> InvoiceDraft:
    """A small invoice whose arithmetic is right: 2 x 50.00 at 19 %."""
    base = dict(
        number="RE-1",
        issue_date=date(2026, 3, 1),
        due_date=date(2026, 3, 15),
        currency="EUR",
        seller=PartyDraft(name="Muster GmbH", vat_id="DE123456789"),
        payment_ibans=(IBAN,),
        lines=(
            LineDraft(
                line_id="1",
                description="Papier",
                quantity=Decimal("2"),
                unit_price=Decimal("50.00"),
                net=Decimal("100.00"),
                vat_rate=Decimal("19"),
            ),
        ),
        vat_breakdown=(
            VatBreakdownDraft(
                category="S",
                rate=Decimal("19"),
                taxable_amount=Decimal("100.00"),
                tax_amount=Decimal("19.00"),
            ),
        ),
        line_net_total=Decimal("100.00"),
        net_total=Decimal("100.00"),
        vat_total=Decimal("19.00"),
        gross_total=Decimal("119.00"),
        amount_due=Decimal("119.00"),
        source_format=DocumentFormat.PDF_TEXT,
    )
    return InvoiceDraft(**(base | fields))


TEXT = """Muster GmbH   USt-IdNr. DE 123 456 789
Rechnung RE-1 vom 01.03.2026, zahlbar bis 15.03.2026
1  Papier   2   50,00   100,00   19 %
Netto 100,00 EUR   USt 19 % 19,00   Gesamt 119,00 EUR
IBAN DE89 3704 0044 0532 0130 00"""


def check_of(invoice: InvoiceDraft, path: str) -> Check:
    return assess(invoice, None)[path].check


def ground_of(invoice: InvoiceDraft, path: str, text: str | None = TEXT) -> Ground:
    return assess(invoice, text)[path].ground


def test_consistent_invoice_passes_every_check_it_has():
    evidence = assess(draft(), None)
    checked = (
        "payment_ibans",
        "seller.vat_id",
        "due_date",
        "lines.0.net",
        "line_net_total",
        "vat_breakdown.0.tax_amount",
        "net_total",
        "vat_total",
        "gross_total",
    )
    assert {evidence[path].check for path in checked} == {Check.PASSED}


def test_field_without_any_rule_is_unchecked():
    assert check_of(draft(), "number") is Check.NONE


def test_missing_field_gets_no_confidence_at_all():
    assert "po_ref" not in assess(draft(), TEXT)


def test_bad_iban_checksum_fails_the_ibans():
    assert check_of(draft(payment_ibans=("DE89370400440532013001",)), "payment_ibans") is (
        Check.FAILED
    )


def test_one_bad_iban_among_several_fails_the_field():
    ibans = (IBAN, "DE00000000000000000000")
    assert check_of(draft(payment_ibans=ibans), "payment_ibans") is Check.FAILED


@pytest.mark.parametrize("vat_id", ["DE12345678", "DE1234567890", "12345", "DEABCDEFGHI"])
def test_malformed_vat_id_fails(vat_id):
    invoice = draft(seller=PartyDraft(name="Muster GmbH", vat_id=vat_id))
    assert check_of(invoice, "seller.vat_id") is Check.FAILED


def test_foreign_vat_id_in_a_plausible_shape_passes():
    invoice = draft(seller=PartyDraft(name="Muster BV", vat_id="NL123456789B01"))
    assert check_of(invoice, "seller.vat_id") is Check.PASSED


def test_due_date_before_the_invoice_date_fails():
    assert check_of(draft(due_date=date(2026, 2, 1)), "due_date") is Check.FAILED


def test_line_whose_net_is_not_quantity_times_price_fails_all_three_cells():
    bad = draft().lines[0].model_copy(update={"net": Decimal("110.00")})
    evidence = assess(draft(lines=(bad,)), None)
    cells = ("lines.0.quantity", "lines.0.unit_price", "lines.0.net")
    assert {evidence[cell].check for cell in cells} == {Check.FAILED}


def test_line_amount_may_differ_by_rounding():
    line = LineDraft(quantity=Decimal("3"), unit_price=Decimal("0.333"), net=Decimal("1.00"))
    assert check_of(draft(lines=(line,), line_net_total=Decimal("1.00")), "lines.0.net") is (
        Check.PASSED
    )


def test_lines_not_summing_to_the_net_lower_the_totals():
    assert check_of(draft(line_net_total=Decimal("90.00")), "line_net_total") is Check.FAILED


def test_net_total_that_is_not_the_sum_of_the_vat_groups_fails():
    assert check_of(draft(net_total=Decimal("101.00")), "net_total") is Check.FAILED


def test_vat_group_whose_tax_is_not_rate_times_base_fails():
    group = draft().vat_breakdown[0].model_copy(update={"tax_amount": Decimal("7.00")})
    assert check_of(draft(vat_breakdown=(group,)), "vat_breakdown.0.tax_amount") is Check.FAILED


def test_gross_that_is_not_net_plus_vat_fails():
    assert check_of(draft(gross_total=Decimal("120.00")), "gross_total") is Check.FAILED


def test_check_that_cannot_be_made_is_not_a_failure():
    # No VAT total: gross cannot be checked against net + VAT.
    assert check_of(draft(vat_total=None), "gross_total") is Check.NONE


@pytest.mark.parametrize(
    "path",
    [
        "number",
        "issue_date",
        "due_date",
        "seller.name",
        "seller.vat_id",
        "payment_ibans",
        "lines.0.description",
        "lines.0.unit_price",
        "lines.0.vat_rate",
        "gross_total",
    ],
)
def test_value_printed_in_the_document_is_grounded(path):
    assert ground_of(draft(), path) is Ground.FOUND


@pytest.mark.parametrize(
    ("fields", "path"),
    [
        ({"number": "RE-7"}, "number"),
        ({"gross_total": Decimal("191.00")}, "gross_total"),
        ({"issue_date": date(2026, 3, 2)}, "issue_date"),
        ({"payment_ibans": ("DE02120300000000202051",)}, "payment_ibans"),
    ],
    ids=["text", "amount", "date", "iban"],
)
def test_value_that_is_nowhere_in_the_document_is_not_grounded(fields, path):
    assert ground_of(draft(**fields), path) is Ground.NOT_FOUND


@pytest.mark.parametrize(
    "printed",
    ["1.234,50", "1,234.50", "1 234,50", "1'234.50", "1234,5", "1234.50 €"],
)
def test_amount_is_found_however_it_is_printed(printed):
    invoice = draft(gross_total=Decimal("1234.50"))
    assert ground_of(invoice, "gross_total", f"Summe {printed}") is Ground.FOUND


@pytest.mark.parametrize(
    "printed", ["3. August 2026", "03.08.26", "August 3, 2026", "3 Aug 2026", "08/03/2026"]
)
def test_date_is_found_however_it_is_printed(printed):
    invoice = draft(issue_date=date(2026, 8, 3))
    assert ground_of(invoice, "issue_date", f"Datum: {printed}") is Ground.FOUND


def test_without_document_text_grounding_is_unknown():
    assert ground_of(draft(), "number", None) is Ground.UNKNOWN


def test_value_filled_in_by_arithmetic_is_checked_but_not_looked_for_in_the_text():
    invoice = draft(derived_fields=("vat_total",))
    evidence = assess(invoice, TEXT)["vat_total"]
    assert (evidence.check, evidence.ground) == (Check.PASSED, Ground.UNKNOWN)


CALIBRATION = Calibration(
    version="test",
    probabilities={
        "pdf_text/passed/found": 0.99,
        "pdf_text/none/found": 0.97,
        "pdf_text/failed/found": 0.40,
    },
    fallback=0.70,
)


def test_score_maps_each_fields_evidence_to_its_calibrated_probability():
    confidence = score(
        draft(gross_total=Decimal("120.00")), TEXT.replace("119,00", "120,00"), CALIBRATION
    )
    assert (confidence["number"], confidence["net_total"], confidence["gross_total"]) == (
        0.97,
        0.99,
        0.40,
    )


def test_evidence_the_calibration_never_saw_gets_the_fallback():
    assert score(draft(), None, CALIBRATION)["number"] == 0.70


def test_assumed_currency_is_marked_uncertain_whatever_the_calibration_says():
    invoice = draft(derived_fields=("currency",))
    assert score(invoice, TEXT, CALIBRATION)["currency"] == ASSUMED_CONFIDENCE


def test_bucket_names_format_check_and_grounding():
    evidence = assess(draft(), TEXT)["gross_total"]
    assert bucket(DocumentFormat.PDF_TEXT, evidence) == "pdf_text/passed/found"


class OneDraft:
    def extract(self, data: bytes) -> InvoiceDraft:
        return draft()


def test_scoring_extractor_attaches_a_confidence_to_every_field_that_has_a_value():
    scored = ScoringExtractor(OneDraft(), CALIBRATION, text_of=lambda data: TEXT).extract(b"")
    assert (scored.field_confidence["number"], "po_ref" in scored.field_confidence) == (
        0.97,
        False,
    )


def test_scoring_still_works_when_the_document_has_no_text():
    scored = ScoringExtractor(OneDraft(), CALIBRATION, text_of=lambda data: None).extract(b"")
    assert scored.field_confidence["number"] == CALIBRATION.fallback


def test_shipped_calibration_loads_and_distrusts_a_failed_check_on_a_scan():
    shipped = Calibration.load()
    assert shipped.probabilities["image/failed/not_found"] < 0.5 < shipped.fallback + 0.01
