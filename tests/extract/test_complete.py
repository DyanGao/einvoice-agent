from decimal import Decimal

import pytest

from app.extract import CompletingExtractor, InvoiceDraft, VatBreakdownDraft, complete


def group(tax_amount: str | None) -> VatBreakdownDraft:
    return VatBreakdownDraft(
        category="S",
        rate=Decimal("19"),
        taxable_amount=Decimal("100.00"),
        tax_amount=None if tax_amount is None else Decimal(tax_amount),
    )


def test_vat_total_not_printed_is_the_sum_of_the_vat_groups():
    draft = InvoiceDraft(currency="EUR", vat_breakdown=(group("19.00"), group("7.01")))
    assert complete(draft).vat_total == Decimal("26.01")


def test_printed_vat_total_is_never_replaced_even_if_it_does_not_add_up():
    draft = InvoiceDraft(vat_total=Decimal("1.00"), vat_breakdown=(group("19.00"),))
    assert complete(draft).vat_total == Decimal("1.00")


@pytest.mark.parametrize(
    "groups", [(), (group("19.00"), group(None))], ids=["no-groups", "one-amount-unread"]
)
def test_vat_total_stays_missing_when_the_sum_would_be_a_guess(groups):
    assert complete(InvoiceDraft(vat_breakdown=groups)).vat_total is None


def test_invoice_without_a_stated_currency_is_taken_to_be_in_euro():
    assert complete(InvoiceDraft()).currency == "EUR"


def test_stated_currency_is_kept():
    assert complete(InvoiceDraft(currency="CHF")).currency == "CHF"


def test_complete_draft_is_returned_unchanged():
    draft = InvoiceDraft(currency="EUR", vat_total=Decimal("19.00"))
    assert complete(draft) is draft


class OneDraft:
    def extract(self, data: bytes) -> InvoiceDraft:
        return InvoiceDraft(vat_breakdown=(group("19.00"),))


def test_completing_extractor_applies_the_rules_to_what_the_inner_extractor_read():
    draft = CompletingExtractor(OneDraft()).extract(b"%PDF-")
    assert (draft.vat_total, draft.currency) == (Decimal("19.00"), "EUR")


def test_filled_in_fields_are_named_so_nobody_takes_them_for_read_values():
    draft = complete(InvoiceDraft(vat_breakdown=(group("19.00"),)))
    assert draft.derived_fields == ("vat_total", "currency")


def test_read_values_are_not_named_as_filled_in():
    assert complete(InvoiceDraft(currency="EUR", vat_total=Decimal("1.00"))).derived_fields == ()


# --- zero-rate categories and the gross total -------------------------------


def zero_rated(category: str = "AE", tax_amount: str | None = None) -> VatBreakdownDraft:
    return VatBreakdownDraft(
        category=category,
        taxable_amount=Decimal("500.00"),
        tax_amount=None if tax_amount is None else Decimal(tax_amount),
    )


@pytest.mark.parametrize("category", ["Z", "E", "AE", "K", "G", "O"])
def test_zero_rate_category_without_a_tax_amount_carries_no_tax(category):
    completed = complete(InvoiceDraft(vat_breakdown=(zero_rated(category),)))
    assert completed.vat_breakdown[0].tax_amount == Decimal("0")
    assert completed.vat_total == Decimal("0")


def test_tax_filled_in_for_a_zero_rate_category_is_marked_derived():
    completed = complete(InvoiceDraft(vat_breakdown=(zero_rated(),)))
    assert "vat_breakdown.0.tax_amount" in completed.derived_fields
    assert "vat_total" in completed.derived_fields


def test_standard_rate_without_a_tax_amount_stays_unread():
    standard = group(None)
    assert complete(InvoiceDraft(vat_breakdown=(standard,))).vat_breakdown[0].tax_amount is None


def test_printed_tax_amount_of_a_zero_rate_category_is_kept():
    completed = complete(InvoiceDraft(vat_breakdown=(zero_rated("E", "3.00"),)))
    assert completed.vat_breakdown[0].tax_amount == Decimal("3.00")


def test_gross_total_not_read_is_net_plus_vat():
    draft = InvoiceDraft(net_total=Decimal("100.00"), vat_total=Decimal("19.00"))
    completed = complete(draft)
    assert completed.gross_total == Decimal("119.00")
    assert "gross_total" in completed.derived_fields


def test_reverse_charge_invoice_gets_vat_zero_and_gross_equal_to_net():
    draft = InvoiceDraft(net_total=Decimal("500.00"), vat_breakdown=(zero_rated("AE"),))
    completed = complete(draft)
    assert (completed.vat_total, completed.gross_total) == (Decimal("0"), Decimal("500.00"))


@pytest.mark.parametrize(
    "draft",
    [
        InvoiceDraft(net_total=Decimal("100.00")),
        InvoiceDraft(vat_total=Decimal("19.00")),
        InvoiceDraft(net_total=Decimal("1"), vat_total=Decimal("1"), gross_total=Decimal("5")),
    ],
    ids=["no-vat", "no-net", "printed-gross"],
)
def test_gross_total_is_derived_only_from_both_parts_and_never_replaced(draft):
    assert complete(draft).gross_total == draft.gross_total


# --- § 19 UStG: small businesses charge no VAT, and say so on the invoice -----

SMALL_BUSINESS_NOTES = [
    "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet.",
    "Gemäß $ 19 USIG wird keine Umsatzsteuer berechnet.",  # as OCR reads it
    "Kein Ausweis der Umsatzsteuer aufgrund der Kleinunternehmerregelung.",
    "Steuerfrei nach § 19 Abs. 1 UStG",
]


@pytest.mark.parametrize("note", SMALL_BUSINESS_NOTES)
def test_small_business_note_without_vat_read_means_exempt_with_no_tax(note):
    draft = InvoiceDraft(net_total=Decimal("250.00"))
    completed = complete(draft, text=f"Rechnung\nSumme 250,00\n{note}\n")
    (exempt,) = completed.vat_breakdown
    assert (exempt.category, exempt.rate, exempt.taxable_amount, exempt.tax_amount) == (
        "E",
        Decimal("0"),
        Decimal("250.00"),
        Decimal("0"),
    )
    assert (completed.vat_total, completed.gross_total) == (Decimal("0"), Decimal("250.00"))
    assert "vat_breakdown" in completed.derived_fields


@pytest.mark.parametrize(
    "draft",
    [
        InvoiceDraft(net_total=Decimal("100.00"), vat_total=Decimal("19.00")),
        InvoiceDraft(net_total=Decimal("100.00"), vat_breakdown=(group("19.00"),)),
    ],
    ids=["vat-total-read", "vat-group-read"],
)
def test_small_business_note_never_overrides_vat_that_was_read(draft):
    completed = complete(draft, text=SMALL_BUSINESS_NOTES[0])
    assert completed.vat_breakdown == draft.vat_breakdown
    assert completed.vat_total == (draft.vat_total or Decimal("19.00"))


@pytest.mark.parametrize(
    "text",
    [None, "Rechnung\nNetto 100,00\nUSt 19 % 19,00\n", "Zahlbar innerhalb von 19 Tagen"],
    ids=["no-text", "ordinary-invoice", "unrelated-19"],
)
def test_without_a_small_business_note_nothing_is_assumed(text):
    completed = complete(InvoiceDraft(net_total=Decimal("100.00")), text=text)
    assert (completed.vat_breakdown, completed.vat_total) == ((), None)


def test_completing_extractor_reads_the_note_from_the_document_text():
    class Fixed:
        def extract(self, data):
            return InvoiceDraft(net_total=Decimal("80.00"))

    extractor = CompletingExtractor(Fixed(), text_of=lambda data: SMALL_BUSINESS_NOTES[0])
    assert extractor.extract(b"%PDF").vat_total == Decimal("0")
