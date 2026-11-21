"""One sign convention for credit notes (ARCHITECTURE.md, D13).

An XRechnung credit note (381) carries positive amounts: its type says that
the money goes the other way. A PDF credit note often prints the same amounts
negative. The draft keeps them as XRechnung does — the type, and positive
amounts — so that the checks, the matcher and the review page see one form;
the booking (Stage 5, increment 3) gives them their sign.

Here, in one place:
- a credit note whose totals are negative gets every amount made positive;
- a document read as an ordinary invoice (or with no type) whose totals are
  all negative is taken for a credit note: its type becomes 381 and is marked
  as concluded (derived), so a person sees it was not read.

A discount line on an ordinary invoice is negative and stays so: only
negative totals make a credit note.
"""

from decimal import Decimal

from app.extract.draft import InvoiceDraft
from app.models.invoice import CREDIT_NOTE, INVOICE

_TOTALS = ("line_net_total", "net_total", "vat_total", "gross_total", "amount_due")


def _negative_totals(draft: InvoiceDraft) -> bool:
    """Totals were read and none of them is positive, at least one negative."""
    totals = [getattr(draft, name) for name in _TOTALS if getattr(draft, name) is not None]
    return any(value < 0 for value in totals) and all(value <= 0 for value in totals)


def _positive(value: Decimal | None) -> Decimal | None:
    return None if value is None else abs(value)


def positive_credit(draft: InvoiceDraft) -> tuple[InvoiceDraft, list[str]]:
    """The draft in the convention, and the fields it concluded rather than read."""
    if not _negative_totals(draft):
        return draft, []
    concluded: list[str] = []
    type_code = draft.type_code
    if type_code in (None, INVOICE):
        type_code = CREDIT_NOTE
        concluded.append("type_code")
    elif type_code != CREDIT_NOTE:
        # Another type (a corrected invoice, a self-billed one) with negative
        # totals: not ours to turn around. A person reviews every other type.
        return draft, []
    updates = {name: _positive(getattr(draft, name)) for name in _TOTALS}
    updates["lines"] = tuple(
        line.model_copy(
            update={
                "quantity": _positive(line.quantity),
                "unit_price": _positive(line.unit_price),
                "net": _positive(line.net),
            }
        )
        for line in draft.lines
    )
    updates["vat_breakdown"] = tuple(
        group.model_copy(
            update={
                "taxable_amount": _positive(group.taxable_amount),
                "tax_amount": _positive(group.tax_amount),
            }
        )
        for group in draft.vat_breakdown
    )
    return draft.model_copy(update={**updates, "type_code": type_code}), concluded
