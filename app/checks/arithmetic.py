"""The arithmetic an invoice has to satisfy within itself.

One definition, two users: the checks report a rule that does not hold as a
finding, and the confidence of the fields involved rests on the same rules
(app/extract/confidence.py). A rule is only evaluated when all its inputs are
present; a missing value is not a wrong sum.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.extract.draft import InvoiceDraft
from app.texts import Text

# Printed amounts are rounded to cents, so a product or sum may be off by one.
TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class RuleResult:
    rule: str
    description: str
    # The fields this rule vouches for when it holds, or puts in doubt when not.
    fields: tuple[str, ...]
    expected: Decimal
    actual: Decimal

    @property
    def holds(self) -> bool:
        """Within a cent of the expected amount as an invoice would print it.
        Compared with the unrounded product, a tax of 159.40 would be 1.09
        cents from 159.3891 and fail, although it is one cent from 159.39."""
        printed = self.expected.quantize(TOLERANCE, rounding=ROUND_HALF_UP)
        return abs(printed - self.actual) <= TOLERANCE


def evaluate(draft: InvoiceDraft) -> list[RuleResult]:
    results = []

    for index, line in enumerate(draft.lines):
        if None not in (line.quantity, line.unit_price, line.net):
            results.append(
                RuleResult(
                    rule="line_amount",
                    description=Text("check.line_net", line=index + 1),
                    fields=tuple(
                        f"lines.{index}.{cell}" for cell in ("quantity", "unit_price", "net")
                    ),
                    expected=line.quantity * line.unit_price,
                    actual=line.net,
                )
            )
    nets = [line.net for line in draft.lines]
    if draft.line_net_total is not None and nets and None not in nets:
        results.append(
            RuleResult(
                rule="line_sum",
                description=Text("check.lines_total"),
                fields=("line_net_total",),
                expected=sum(nets, Decimal(0)),
                actual=draft.line_net_total,
            )
        )

    for index, group in enumerate(draft.vat_breakdown):
        if None not in (group.rate, group.taxable_amount, group.tax_amount):
            results.append(
                RuleResult(
                    rule="vat_group_tax",
                    description=Text("check.vat_group", group=index + 1),
                    fields=tuple(
                        f"vat_breakdown.{index}.{cell}"
                        for cell in ("rate", "taxable_amount", "tax_amount")
                    ),
                    expected=group.taxable_amount * group.rate / 100,
                    actual=group.tax_amount,
                )
            )
    bases = [group.taxable_amount for group in draft.vat_breakdown]
    taxes = [group.tax_amount for group in draft.vat_breakdown]
    if draft.net_total is not None and bases and None not in bases:
        results.append(
            RuleResult(
                rule="net_sum",
                description=Text("check.net_total"),
                fields=("net_total",),
                expected=sum(bases, Decimal(0)),
                actual=draft.net_total,
            )
        )
    if draft.vat_total is not None and taxes and None not in taxes:
        results.append(
            RuleResult(
                rule="vat_sum",
                description=Text("check.vat_total"),
                fields=("vat_total",),
                expected=sum(taxes, Decimal(0)),
                actual=draft.vat_total,
            )
        )
    if None not in (draft.net_total, draft.vat_total, draft.gross_total):
        results.append(
            RuleResult(
                rule="gross",
                description=Text("check.gross_total"),
                fields=("gross_total",),
                expected=draft.net_total + draft.vat_total,
                actual=draft.gross_total,
            )
        )
    return results
