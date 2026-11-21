"""The invoice a credit note corrects (v1.0 Stage 5, increment 2).

A credit note (381) names the invoice it corrects in BT-25, or it does not
and is found by what it says: the same supplier and the same total (a full
credit), or lines the invoice billed (a partial one). Only invoices of the
same supplier that were not rejected count, never another credit note.

    - named, and that invoice is on file: linked, on the reference;
    - named, and it is not: not linked — the reference is not guessed away;
    - not named: one invoice of the same total, or else one whose lines take in
      every credited line, issued no later than the credit note: linked, by
      content; several: none chosen;
    - nothing fits: not linked.

How much of the invoice earlier credit notes already took back is counted by
whoever stores the links (app/checks/service.py); a credit for more than is
left is a finding (rules.credit_link).
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from app.checks.context import KnownInvoice
from app.extract.draft import InvoiceDraft
from app.matching import Basis
from app.models.invoice import CREDIT_NOTE
from app.texts import Text

_NOT_ALNUM = re.compile(r"[^a-z0-9]")


def _key(text: str | None) -> str:
    return _NOT_ALNUM.sub("", (text or "").casefold())


def same_seller(draft: InvoiceDraft, known: KnownInvoice) -> bool:
    if draft.seller.vat_id and known.seller_vat_id:
        return draft.seller.vat_id == known.seller_vat_id
    return bool(_key(draft.seller.name)) and _key(draft.seller.name) == _key(known.seller_name)


@dataclass(frozen=True)
class CreditLink:
    """What the checks found for a credit note."""

    basis: Basis
    # For a person: which invoice and on what grounds, or why none.
    explanation: Text
    invoice: KnownInvoice | None = None
    # How much of the invoice earlier credit notes took back (not rejected ones).
    credited_before: Decimal = Decimal(0)

    @property
    def open_amount(self) -> Decimal | None:
        """What of the invoice is left to credit."""
        if self.invoice is None or self.invoice.gross_total is None:
            return None
        return self.invoice.gross_total - self.credited_before


def _issued_before(draft: InvoiceDraft, known: KnownInvoice) -> bool:
    return (
        draft.issue_date is None or known.issue_date is None or known.issue_date <= draft.issue_date
    )


def _lines_within(draft: InvoiceDraft, known: KnownInvoice) -> bool:
    """Every credited line is a line of the invoice: same description and unit
    price, no more than it billed."""
    lines = [line for line in draft.lines if line.description and line.unit_price is not None]
    if not lines or not known.lines:
        return False
    for line in lines:
        if not any(
            _key(line.description) == _key(billed.description)
            and line.unit_price == billed.unit_price
            and (line.quantity is None or line.quantity <= billed.quantity)
            for billed in known.lines
        ):
            return False
    return True


def link_credit(draft: InvoiceDraft, known: tuple[KnownInvoice, ...]) -> CreditLink | None:
    """The invoice this credit note corrects; None for anything but a credit note."""
    if draft.type_code != CREDIT_NOTE:
        return None
    invoices = [
        invoice
        for invoice in known
        if invoice.type_code != CREDIT_NOTE and not invoice.rejected and same_seller(draft, invoice)
    ]
    if draft.preceding_ref:
        named = [
            invoice for invoice in invoices if _key(invoice.number) == _key(draft.preceding_ref)
        ]
        if len(named) == 1:
            return CreditLink(
                Basis.EXACT_REFERENCE,
                Text("credit.reference", number=named[0].number),
                invoice=named[0],
            )
        if not named:
            return CreditLink(
                Basis.NONE, Text("credit.reference_not_found", number=draft.preceding_ref)
            )
        return CreditLink(
            Basis.AMBIGUOUS,
            Text("credit.ambiguous", numbers=sorted({i.number or "" for i in named})),
        )
    earlier = [invoice for invoice in invoices if _issued_before(draft, invoice)]
    by_total = [
        invoice
        for invoice in earlier
        if draft.gross_total is not None and invoice.gross_total == draft.gross_total
    ]
    if len(by_total) == 1:
        return CreditLink(
            Basis.CONTENT, Text("credit.by_total", number=by_total[0].number), invoice=by_total[0]
        )
    by_lines = [invoice for invoice in earlier if _lines_within(draft, invoice)]
    if not by_total and len(by_lines) == 1:
        return CreditLink(
            Basis.CONTENT, Text("credit.by_lines", number=by_lines[0].number), invoice=by_lines[0]
        )
    candidates = by_total or by_lines
    if candidates:
        return CreditLink(
            Basis.AMBIGUOUS,
            Text("credit.ambiguous", numbers=sorted({i.number or "" for i in candidates})),
        )
    return CreditLink(Basis.NONE, Text("credit.not_found"))
