"""Fixed rules that fill what an invoice implies but does not print.

The model is told to transcribe and never to calculate, so that the checks
after it see the printed values. Some invoices leave out a value that follows
from the rest: several VAT rates without a VAT total, amounts without a
currency. Those gaps are closed here, in code, where the rule is visible and
testable — not by asking the model to do arithmetic.

A printed value is never replaced.
"""

from decimal import Decimal

from app.extract.base import Extractor
from app.extract.draft import InvoiceDraft

# Accounts payable in Germany: an invoice that names no currency is in euro.
DEFAULT_CURRENCY = "EUR"


def complete(draft: InvoiceDraft) -> InvoiceDraft:
    updates: dict[str, object] = {}
    tax_amounts = [group.tax_amount for group in draft.vat_breakdown]
    # Only when every group's tax amount was read: a partial sum is a wrong total.
    if draft.vat_total is None and tax_amounts and None not in tax_amounts:
        updates["vat_total"] = sum(tax_amounts, Decimal(0))
    if draft.currency is None:
        updates["currency"] = DEFAULT_CURRENCY
    return draft.model_copy(update=updates) if updates else draft


class CompletingExtractor:
    """An extractor followed by the completion rules."""

    def __init__(self, inner: Extractor) -> None:
        self._inner = inner

    def extract(self, data: bytes) -> InvoiceDraft:
        return complete(self._inner.extract(data))
