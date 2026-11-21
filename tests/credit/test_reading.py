"""Credit notes are read (v1.0 Stage 5, increment 1): the invoice they correct
(BT-25), and one sign convention — amounts positive, the type says it is a
credit (ARCHITECTURE.md, D13)."""

from decimal import Decimal

import pytest

from app.extract.baseline import extract_from_text
from app.extract.complete import complete
from app.extract.draft import InvoiceDraft, LineDraft, VatBreakdownDraft
from app.models import DocumentFormat
from app.parse import parse_invoice
from eval.generators.canonical import XRECHNUNG, build_invoice, credit_note_for
from eval.generators.xrechnung import render_cii, render_ubl


@pytest.mark.parametrize(
    ("render", "source"), [(render_ubl, DocumentFormat.UBL), (render_cii, DocumentFormat.CII)]
)
def test_a_generated_credit_note_reads_back_as_written(render, source):
    invoice = build_invoice(7, 1)
    credit = credit_note_for(invoice, "GS-2026-00001")
    parsed = parse_invoice(render(credit, XRECHNUNG))
    assert parsed == credit.to_gold(source)
    assert parsed.is_credit_note
    assert parsed.preceding_ref == invoice.number
    assert parsed.gross_total > 0


def test_a_ubl_credit_note_is_its_own_document_type():
    xml = render_ubl(credit_note_for(build_invoice(7, 2), "GS-1"), XRECHNUNG)
    assert b"<ubl:CreditNote " in xml
    assert b"<cbc:CreditNoteTypeCode>381</cbc:CreditNoteTypeCode>" in xml
    assert b"<cac:CreditNoteLine>" in xml


def _negative_credit(type_code: str | None) -> InvoiceDraft:
    """A credit note as a PDF often prints it: every amount negative."""
    return InvoiceDraft(
        number="GS-17",
        type_code=type_code,
        lines=(
            LineDraft(
                line_id="1",
                description="Rückgabe",
                quantity=Decimal("-2"),
                unit_price=Decimal("50.00"),
                net=Decimal("-100.00"),
                vat_rate=Decimal("19"),
            ),
        ),
        vat_breakdown=(
            VatBreakdownDraft(
                category="S",
                rate=Decimal("19"),
                taxable_amount=Decimal("-100.00"),
                tax_amount=Decimal("-19.00"),
            ),
        ),
        line_net_total=Decimal("-100.00"),
        net_total=Decimal("-100.00"),
        vat_total=Decimal("-19.00"),
        gross_total=Decimal("-119.00"),
        amount_due=Decimal("-119.00"),
        currency="EUR",
    )


def test_a_credit_note_printed_negative_is_stored_positive():
    draft = complete(_negative_credit("381"))
    assert (draft.gross_total, draft.net_total, draft.vat_total, draft.amount_due) == (
        Decimal("119.00"),
        Decimal("100.00"),
        Decimal("19.00"),
        Decimal("119.00"),
    )
    line = draft.lines[0]
    assert (line.quantity, line.unit_price, line.net) == (
        Decimal("2"),
        Decimal("50.00"),
        Decimal("100.00"),
    )
    group = draft.vat_breakdown[0]
    assert (group.taxable_amount, group.tax_amount) == (Decimal("100.00"), Decimal("19.00"))
    # The type was read, so it is not marked; the sign is a convention, not a guess.
    assert "type_code" not in draft.derived_fields


def test_an_invoice_with_only_negative_totals_is_taken_for_a_credit_note_and_marked():
    draft = complete(_negative_credit("380"))
    assert draft.type_code == "381"
    assert draft.gross_total == Decimal("119.00")
    # A person sees that the type was not read but concluded.
    assert "type_code" in draft.derived_fields


def test_a_positive_invoice_is_left_as_it_is():
    draft = InvoiceDraft(
        type_code="380", net_total=Decimal("100"), gross_total=Decimal("119"), currency="EUR"
    )
    assert complete(draft) == draft


def test_an_invoice_with_a_negative_line_but_a_positive_total_stays_an_invoice():
    # A discount line is negative on an ordinary invoice (03.06a has one).
    draft = InvoiceDraft(
        type_code="380",
        lines=(LineDraft(line_id="2", net=Decimal("-10.00")),),
        net_total=Decimal("90.00"),
        gross_total=Decimal("107.10"),
        currency="EUR",
    )
    completed = complete(draft)
    assert completed.type_code == "380"
    assert completed.lines[0].net == Decimal("-10.00")


TEXT_CREDIT = """Muster Elektro GmbH
USt-IdNr.: DE123456789
Praxis Dr. Äpfelmann

GUTSCHRIFT
Gutschrift Nr.: GS-2026-0042
Datum: 12.03.2026
zu Rechnung Nr.: RE-2026-01234

1  Rückgabe Kabel    2  Stk  50,00   100,00
Nettobetrag                          100,00
USt 19 %                              19,00
Gesamtbetrag                         119,00
"""


def test_the_rule_based_reader_reads_a_credit_note_and_the_invoice_it_names():
    draft = extract_from_text(TEXT_CREDIT)
    assert draft.type_code == "381"
    assert draft.number == "GS-2026-0042"
    assert draft.preceding_ref == "RE-2026-01234"


def test_the_rule_based_reader_still_reads_an_invoice_number():
    draft = extract_from_text(
        TEXT_CREDIT.replace("GUTSCHRIFT", "RECHNUNG")
        .replace("Gutschrift Nr.: GS-2026-0042", "Rechnung Nr.: RE-2026-0042")
        .replace("zu Rechnung Nr.: RE-2026-01234\n", "")
    )
    assert (draft.type_code, draft.number, draft.preceding_ref) == ("380", "RE-2026-0042", None)
