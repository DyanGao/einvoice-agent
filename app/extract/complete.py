"""Fixed rules that fill what an invoice implies but does not print.

The model is told to transcribe and never to calculate, so that the checks
after it see the printed values. Some invoices leave out a value that follows
from the rest: several VAT rates without a VAT total, a reverse-charge invoice
without a tax line, a small business's note that it charges no VAT, no total
with VAT, amounts without a currency. Those gaps are closed here, in code,
where the rule is visible and testable — not by asking the model to do
arithmetic.

A printed value is never replaced, and every field filled in here is named in
the draft's derived_fields so that nobody takes it for a value that was read.
"""

import re
from collections.abc import Callable
from decimal import Decimal

from app.extract.base import Extractor
from app.extract.draft import InvoiceDraft, VatBreakdownDraft

# Accounts payable in Germany: an invoice that names no currency is in euro.
DEFAULT_CURRENCY = "EUR"

# EN 16931 categories that carry no VAT: zero-rated, exempt (also § 19 UStG),
# reverse charge (§ 13b UStG), intra-community supply, export, not subject.
ZERO_RATE_CATEGORIES = frozenset({"Z", "E", "AE", "K", "G", "O"})

# A small business under § 19 UStG charges no VAT and must say so on the
# invoice. The model tends to read no VAT category for it, so the note is
# looked for in the document's own text, as OCR may render it: "§" as "$",
# "UStG" as "USIG".
_SMALL_BUSINESS = re.compile(
    r"Kleinunternehmer|(?:§|\$|\b)\s*19\s*(?:Abs\.?\s*1\s*)?U\s*[S5][tTiIl1][GC]\b"
)


def _small_business_exemption(draft: InvoiceDraft, text: str | None) -> VatBreakdownDraft | None:
    """An exempt VAT group for a § 19 note, when no VAT at all was read."""
    if text is None or draft.vat_breakdown or draft.vat_total not in (None, Decimal(0)):
        return None
    if not _SMALL_BUSINESS.search(text):
        return None
    return VatBreakdownDraft(
        category="E", rate=Decimal(0), taxable_amount=draft.net_total, tax_amount=Decimal(0)
    )


def complete(draft: InvoiceDraft, text: str | None = None) -> InvoiceDraft:
    """`text` is the document's own text (PDF text layer or OCR), if any."""
    updates: dict[str, object] = {}
    derived: list[str] = []

    exempt = _small_business_exemption(draft, text)
    if exempt is not None:
        draft = draft.model_copy(update={"vat_breakdown": (exempt,)})
        updates["vat_breakdown"] = draft.vat_breakdown
        derived.append("vat_breakdown")

    # A category that carries no VAT has a tax amount of zero, printed or not
    # (reverse-charge invoices often print no tax line at all).
    groups = list(draft.vat_breakdown)
    for index, group in enumerate(groups):
        if group.category in ZERO_RATE_CATEGORIES and group.tax_amount is None:
            groups[index] = group.model_copy(update={"tax_amount": Decimal(0)})
            derived.append(f"vat_breakdown.{index}.tax_amount")
    if groups != list(draft.vat_breakdown):
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
    """An extractor followed by the completion rules. `text_of` gives the
    document's own text for the rules that look at what is printed."""

    def __init__(
        self, inner: Extractor, text_of: Callable[[bytes], str | None] | None = None
    ) -> None:
        self._inner = inner
        self._text_of = text_of

    def extract(self, data: bytes) -> InvoiceDraft:
        draft = self._inner.extract(data)
        return complete(draft, None if self._text_of is None else self._text_of(data))
