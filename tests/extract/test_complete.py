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
