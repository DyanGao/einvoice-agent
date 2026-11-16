"""Fixed rules that fill what an invoice implies but does not print.

The model is told to transcribe and never to calculate, so that the checks
after it see the printed values. Some invoices leave out a value that follows
from the rest: several VAT rates without a VAT total, a reverse-charge invoice
without a tax line, no total with VAT, amounts without a currency. Those
gaps are closed here, in code, where the rule is visible and testable —
not by asking the model to do arithmetic.

A printed value is never replaced, and every field filled in here is named in
the draft's derived_fields so that nobody takes it for a value that was read.
"""

from decimal import Decimal

from app.extract.base import Extractor
from app.extract.draft import InvoiceDraft

# Accounts payable in Germany: an invoice that names no currency is in euro.
DEFAULT_CURRENCY = "EUR"

# EN 16931 categories that carry no VAT: zero-rated, exempt (also § 19 UStG),
# reverse charge (§ 13b UStG), intra-community supply, export, not subject.
ZERO_RATE_CATEGORIES = frozenset({"Z", "E", "AE", "K", "G", "O"})


def complete(draft: InvoiceDraft) -> InvoiceDraft:
    updates: dict[str, object] = {}
    derived: list[str] = []

    # A category that carries no VAT has a tax amount of zero, printed or not
    # (reverse-charge invoices often print no tax line at all).
    groups = list(draft.vat_breakdown)
    for index, group in enumerate(groups):
        if group.category in ZERO_RATE_CATEGORIES and group.tax_amount is None:
            groups[index] = group.model_copy(update={"tax_amount": Decimal(0)})
            derived.append(f"vat_breakdown.{index}.tax_amount")
    if derived:
        updates["vat_breakdown"] = tuple(groups)

    vat_total = draft.vat_total
    tax_amounts = [group.tax_amount for group in groups]
    # Only when every group's tax amount is known: a partial sum is a wrong total.
    if vat_total is None and tax_amounts and None not in tax_amounts:
        vat_total = updates["vat_total"] = sum(tax_amounts, Decimal(0))
        derived.append("vat_total")

    # The total with VAT is the total without VAT plus the VAT (EN 16931 BR-CO-15).
    if draft.gross_total is None and draft.net_total is not None and vat_total is not None:
        updates["gross_total"] = draft.net_total + vat_total
        derived.append("gross_total")

    if draft.currency is None:
        updates["currency"] = DEFAULT_CURRENCY
        derived.append("currency")
    if not updates:
        return draft
    return draft.model_copy(update={**updates, "derived_fields": (*draft.derived_fields, *derived)})


class CompletingExtractor:
    """An extractor followed by the completion rules."""

    def __init__(self, inner: Extractor) -> None:
        self._inner = inner

    def extract(self, data: bytes) -> InvoiceDraft:
        return complete(self._inner.extract(data))
