"""The credit-note set (v1.0 Stage 5): credit notes for invoices of the
matching world, to measure whether a credit note is read as one, linked to
the invoice it corrects, matched to the money that settled it, and booked as
a credit — never as an invoice.

Each case credits one world invoice, in one of these variants:

- full_reference, partial_reference: the whole invoice or some of its lines
  (perhaps fewer of them), naming the invoice (BT-25);
- reference_reformatted: names it, written differently ("RE202604319" for
  "RE-2026-04319");
- full_no_reference, partial_no_reference: no BT-25; the invoice is found by
  its total, or by its lines;
- unknown_reference: names an invoice that is not on file — the right answer
  is no link, not a guess by content;
- invoice_not_on_file: no BT-25, and the credited invoice never arrived — the
  right answer is no link.

And settled in one of these ways (the transfers are added to the world's
bank account, so the ERP holds them):

- none: no money yet;
- refund_reference, refund_amount: the supplier pays the credit back, naming
  the credit note or its invoice, or with only the amount;
- offset: the next transfer to the supplier pays one of its open invoices
  less the credit.

Every case comes as XRechnung UBL, XRechnung CII, or a text PDF (in the dev
layouts for the dev split, the held-out layouts for the test split), half
of the PDFs printing the amounts negative as many credit notes do.
Everything is invented.

    python -m eval.generators.credits --out eval/out/credits
"""

import argparse
import json
import random
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from eval.generators.canonical import CanonicalInvoice, Item, credit_note_for, money
from eval.generators.pdf import render_pdf
from eval.generators.pdf_layouts import HELD_OUT_LAYOUTS, LAYOUTS
from eval.generators.world import (
    DEFAULT_SEED,
    UNPAID,
    BankTransaction,
    World,
    WorldInvoice,
    _bank_name,
    build_world,
)
from eval.generators.xrechnung import render_cii, render_ubl

FULL_REFERENCE = "full_reference"
PARTIAL_REFERENCE = "partial_reference"
REFERENCE_REFORMATTED = "reference_reformatted"
FULL_NO_REFERENCE = "full_no_reference"
PARTIAL_NO_REFERENCE = "partial_no_reference"
UNKNOWN_REFERENCE = "unknown_reference"
INVOICE_NOT_ON_FILE = "invoice_not_on_file"
VARIANTS = (
    FULL_REFERENCE,
    PARTIAL_REFERENCE,
    REFERENCE_REFORMATTED,
    FULL_NO_REFERENCE,
    PARTIAL_NO_REFERENCE,
    UNKNOWN_REFERENCE,
    INVOICE_NOT_ON_FILE,
)
# The variants whose right answer is no link at all.
NO_LINK = frozenset({UNKNOWN_REFERENCE, INVOICE_NOT_ON_FILE})
PARTIAL = frozenset({PARTIAL_REFERENCE, PARTIAL_NO_REFERENCE})
NAMES_INVOICE = frozenset({FULL_REFERENCE, PARTIAL_REFERENCE, REFERENCE_REFORMATTED})

NONE, REFUND_REFERENCE, REFUND_AMOUNT, OFFSET = (
    "none",
    "refund_reference",
    "refund_amount",
    "offset",
)
SETTLEMENTS = (NONE, REFUND_REFERENCE, REFUND_AMOUNT, OFFSET)

UBL, CII, PDF, PDF_NEGATIVE = "ubl", "cii", "pdf", "pdf_negative"
FORMATS = (UBL, CII, PDF, PDF_NEGATIVE)

CASES = 160
# The proportions the variants and settlements are dealt out in.
_VARIANT_DECK = (
    (FULL_REFERENCE,) * 4
    + (PARTIAL_REFERENCE,) * 4
    + (REFERENCE_REFORMATTED,) * 2
    + (FULL_NO_REFERENCE,) * 3
    + (PARTIAL_NO_REFERENCE,) * 3
    + (UNKNOWN_REFERENCE,) * 2
    + (INVOICE_NOT_ON_FILE,) * 2
)
_SETTLEMENT_DECK = (NONE,) * 4 + (REFUND_REFERENCE,) * 3 + (REFUND_AMOUNT,) * 2 + (OFFSET,) * 3


@dataclass(frozen=True)
class ExpectedPayment:
    bank_transaction_id: str
    amount: Decimal
    credit_note: str | None = None


@dataclass(frozen=True)
class CreditCase:
    id: str
    split: str  # dev | test, the credited invoice's
    variant: str
    settlement: str
    format: str
    # The world invoice credited; the one the credit note must be linked to,
    # unless the variant is in NO_LINK.
    base_id: str
    credit: CanonicalInvoice
    filename: str
    content: bytes
    # How the credit note was settled (empty: not yet).
    payments: tuple[ExpectedPayment, ...]
    # For an offset: the invoice paid less the credit, and that transfer's
    # parts as its match must find them.
    offset_invoice_id: str | None = None
    offset_payments: tuple[ExpectedPayment, ...] = ()

    @property
    def expected_invoice_id(self) -> str | None:
        return None if self.variant in NO_LINK else self.base_id


@dataclass(frozen=True)
class CreditSet:
    seed: int
    # The ERP's data: the world with the refunds and offsets added.
    world: World
    # Invoices already processed when the credit notes arrive.
    on_file: tuple[WorldInvoice, ...]
    cases: tuple[CreditCase, ...]


def _partial_items(items: tuple[Item, ...], rng: random.Random) -> tuple[Item, ...] | None:
    """Some of the lines, perhaps one of them with fewer units; None when the
    invoice has nothing to leave out."""
    kept = list(items)
    if len(kept) > 1:
        kept = rng.sample(kept, rng.randint(1, len(kept) - 1))
        kept.sort(key=lambda item: items.index(item))
    smaller = [
        position
        for position, item in enumerate(kept)
        if item.quantity > 1 and item.quantity == item.quantity.to_integral_value()
    ]
    if smaller and (len(kept) == len(items) or rng.random() < 0.5):
        position = rng.choice(smaller)
        item = kept[position]
        quantity = Decimal(rng.randint(1, int(item.quantity) - 1))
        kept[position] = replace(item, quantity=quantity, net=money(quantity * item.unit_price))
    if len(kept) == len(items) and kept == list(items):
        return None
    return tuple(kept)


def reformatted(number: str) -> str:
    """The same number as another system writes it."""
    plain = number.replace("-", "").replace("/", "")
    return plain if plain != number else number.lower()


def _unknown_number(number: str, taken: set[str], rng: random.Random) -> str:
    """A number like this one that no invoice in the world has."""
    while True:
        digits = [index for index, char in enumerate(number) if char.isdigit()][-3:]
        index = rng.choice(digits)
        candidate = number[:index] + rng.choice("0123456789") + number[index + 1 :]
        if candidate != number and candidate not in taken:
            return candidate


def _render(credit: CanonicalInvoice, fmt: str, split: str, rng: random.Random) -> bytes:
    if fmt == UBL:
        return render_ubl(credit, "xrechnung")
    if fmt == CII:
        return render_cii(credit, "xrechnung")
    layout = rng.choice(HELD_OUT_LAYOUTS if split == "test" else LAYOUTS)
    return render_pdf(credit, layout, negative=fmt == PDF_NEGATIVE)


def _extension(fmt: str) -> str:
    return "xml" if fmt in (UBL, CII) else "pdf"


def build_credit_set(world: World, seed: int = DEFAULT_SEED, count: int = CASES) -> CreditSet:
    rng = random.Random(seed + 381)  # noqa: S311 - reproducible test data, not security
    taken = {entry.invoice.number for entry in world.invoices}
    suppliers = {supplier.id: supplier for supplier in world.suppliers}
    # Open invoices that an offset can pay less a credit.
    open_by_supplier: dict[str, list[WorldInvoice]] = {}
    for entry in world.invoices:
        if entry.payment_kind == UNPAID:
            open_by_supplier.setdefault(entry.supplier_id, []).append(entry)
    pool = list(world.invoices)
    rng.shuffle(pool)
    used: set[str] = set()
    transactions: list[BankTransaction] = []
    cases: list[CreditCase] = []

    def transaction(entry: WorldInvoice, amount: Decimal, on, purpose: str) -> str:
        supplier = suppliers[entry.supplier_id]
        tx = BankTransaction(
            id=f"TX-{len(world.bank_transactions) + len(transactions) + 1:06d}",
            booking_date=on,
            amount=amount,
            counterparty_name=_bank_name(supplier.company.name, rng),
            counterparty_iban=supplier.ibans[0],
            purpose=purpose,
        )
        transactions.append(tx)
        return tx.id

    for index in range(count):
        variant = _VARIANT_DECK[index % len(_VARIANT_DECK)]
        settlement = _SETTLEMENT_DECK[rng.randrange(len(_SETTLEMENT_DECK))]
        fmt = FORMATS[index % len(FORMATS)]
        while True:
            entry = pool.pop()
            if entry.id in used:
                continue
            items = None
            if variant in PARTIAL:
                items = _partial_items(entry.invoice.items, rng)
                if items is None:
                    continue
            break
        used.add(entry.id)
        invoice = entry.invoice
        number = f"GS-{invoice.issue_date.year}-{index + 1:05d}"
        credit = credit_note_for(
            invoice,
            number,
            days_later=rng.randint(3, 40),
            items=items,
            names_invoice=variant in NAMES_INVOICE,
        )
        if variant == REFERENCE_REFORMATTED:
            credit = replace(credit, preceding_ref=reformatted(invoice.number))
        elif variant == UNKNOWN_REFERENCE:
            credit = replace(credit, preceding_ref=_unknown_number(invoice.number, taken, rng))

        partner = None
        if settlement == OFFSET:
            partner = next(
                (
                    other
                    for other in open_by_supplier.get(entry.supplier_id, [])
                    if other.id not in used and other.invoice.gross_total > credit.gross_total
                ),
                None,
            )
            if partner is None:
                settlement = REFUND_REFERENCE
        on = credit.issue_date + timedelta(days=rng.randint(2, 30))
        payments: tuple[ExpectedPayment, ...] = ()
        offset_payments: tuple[ExpectedPayment, ...] = ()
        if settlement == REFUND_REFERENCE:
            purpose = rng.choice(
                (f"Gutschrift {number}", f"Erstattung {number}", f"Rueckzahlung {invoice.number}")
            )
            tx = transaction(entry, -credit.gross_total, on, purpose)
            payments = (ExpectedPayment(tx, credit.gross_total),)
        elif settlement == REFUND_AMOUNT:
            purpose = rng.choice(("Erstattung", "Rueckerstattung Kundenkonto", "Gutschrift"))
            tx = transaction(entry, -credit.gross_total, on, purpose)
            payments = (ExpectedPayment(tx, credit.gross_total),)
        elif settlement == OFFSET:
            used.add(partner.id)
            # After both: a transfer cannot pay an invoice not yet issued.
            on = max(credit.issue_date, partner.invoice.issue_date) + timedelta(
                days=rng.randint(2, 30)
            )
            paid = partner.invoice.gross_total - credit.gross_total
            purpose = rng.choice(
                (
                    f"{partner.invoice.number} abzgl. Gutschrift {number}",
                    f"Rechnung {partner.invoice.number}",
                    partner.invoice.number,
                )
            )
            tx = transaction(entry, paid, on, purpose)
            payments = (ExpectedPayment(tx, credit.gross_total),)
            offset_payments = (
                ExpectedPayment(tx, paid),
                ExpectedPayment(tx, credit.gross_total, credit_note=number),
            )

        case_id = f"credit-{index + 1:04d}"
        filename = f"{case_id}.{_extension(fmt)}"
        cases.append(
            CreditCase(
                id=case_id,
                split=entry.split,
                variant=variant,
                settlement=settlement,
                format=fmt,
                base_id=entry.id,
                credit=credit,
                filename=filename,
                content=_render(credit, fmt, entry.split, rng),
                payments=payments,
                offset_invoice_id=None if partner is None else partner.id,
                offset_payments=offset_payments,
            )
        )

    not_on_file = {case.base_id for case in cases if case.variant == INVOICE_NOT_ON_FILE}
    return CreditSet(
        seed=seed,
        world=replace(world, bank_transactions=world.bank_transactions + tuple(transactions)),
        on_file=tuple(entry for entry in world.invoices if entry.id not in not_on_file),
        cases=tuple(cases),
    )


def write_set(credit_set: CreditSet, out_dir: Path) -> None:
    """The files and what each one is, to look at a failure."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "cases.jsonl").open("w", encoding="utf-8") as cases:
        for case in credit_set.cases:
            (out_dir / case.filename).write_bytes(case.content)
            record = {
                "id": case.id,
                "file": case.filename,
                "split": case.split,
                "variant": case.variant,
                "settlement": case.settlement,
                "format": case.format,
                "credited_invoice": case.base_id,
                "expected_invoice": case.expected_invoice_id,
                "offset_invoice": case.offset_invoice_id,
            }
            cases.write(json.dumps(record) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path("eval/out/credits"))
    args = parser.parse_args()
    credit_set = build_credit_set(build_world(args.seed), args.seed)
    write_set(credit_set, args.out)
    print(f"wrote {len(credit_set.cases)} credit notes to {args.out}")


if __name__ == "__main__":
    main()
