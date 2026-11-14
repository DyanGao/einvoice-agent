"""The exception set: invoices with one planted problem each, and as many
clean ones, to measure whether the checks catch what they should and leave
the rest alone.

Made from the matching world, so every invoice has a supplier, an order and
payments in the ERP. Six kinds of problem (EVAL_PLAN): wrong VAT, totals that
do not add up, a duplicate of an invoice on file, a price above the order, a
changed bank account, a purchase without an order. Each kind comes in several
variants, from blunt to subtle; some clean invoices are near misses (a
one-cent rounding difference, a price rise within the tolerance, the
supplier's second account). Everything is invented.

    python -m eval.generators.exceptions --out eval/out/exceptions
"""

import argparse
import json
import random
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from app.models import DocumentFormat
from eval.generators.canonical import CanonicalInvoice, Item, TaxGroup, german_iban, money
from eval.generators.world import (
    DEFAULT_SEED,
    World,
    WorldInvoice,
    _plain,
    _typo,
    build_world,
)

CLEAN = "clean"
WRONG_VAT = "wrong_vat"
TOTALS = "totals"
DUPLICATE = "duplicate"
PRICE = "price"
IBAN = "iban"
MISSING_PO = "missing_po"
KINDS = (WRONG_VAT, TOTALS, DUPLICATE, PRICE, IBAN, MISSING_PO)

PER_KIND = 20
CLEAN_CASES = 120
# Of the clean invoices, how many are each kind of near miss (the rest are plain).
NEAR_MISSES = 10

# The variants of each kind, in the proportion they are dealt out.
_VARIANTS = {
    WRONG_VAT: ("rate_all_lines",) * 4 + ("rate_one_line",) * 2 + ("vat_id_check_digit",) * 3
    + ("vat_id_too_short",),
    TOTALS: ("gross", "vat_total", "line_amount", "digits_swapped", "two_cents"),
    DUPLICATE: ("resent",) * 3 + ("number_reformatted",) * 2 + ("number_typo",) * 2
    + ("new_number_same_day",) * 2 + ("reissued",),
    PRICE: ("plus_3_percent", "plus_5_percent", "plus_10_percent", "plus_25_percent",
            "all_lines_plus_5_percent"),
    IBAN: ("new_account",) * 4 + ("same_bank_other_account",) * 3 + ("foreign_account",) * 2
    + ("second_account_added",),
    MISSING_PO: ("no_reference",) * 3 + ("unknown_reference",) * 2,
}  # fmt: skip
_OTHER_RATE = {Decimal(19): (Decimal(16), Decimal(20), Decimal(21)),
               Decimal(7): (Decimal(5), Decimal(9), Decimal(10))}  # fmt: skip


@dataclass(frozen=True)
class ExceptionCase:
    id: str
    split: str  # dev | test
    kind: str  # clean or one of KINDS
    variant: str
    # The world invoice this case was made from.
    base_id: str
    invoice: CanonicalInvoice


@dataclass(frozen=True)
class ExceptionSet:
    seed: int
    # The ERP's data: the world without the orders of purchases made without one.
    world: World
    # Invoices already processed when the cases arrive.
    on_file: tuple[WorldInvoice, ...]
    cases: tuple[ExceptionCase, ...]


# --- changing an invoice ----------------------------------------------------


def _with_items(invoice: CanonicalInvoice, items: tuple[Item, ...]) -> CanonicalInvoice:
    """The invoice with these lines and every total following from them."""
    groups = []
    for rate in sorted({item.vat_rate for item in items}, reverse=True):
        taxable = sum((item.net for item in items if item.vat_rate == rate), Decimal(0))
        groups.append(TaxGroup(rate, taxable, money(taxable * rate / 100)))
    net = sum((item.net for item in items), Decimal(0))
    vat = sum((group.tax_amount for group in groups), Decimal(0))
    return replace(
        invoice,
        items=items,
        tax_groups=tuple(groups),
        line_net_total=net,
        net_total=net,
        vat_total=vat,
        gross_total=net + vat,
        amount_due=net + vat,
    )


def raise_prices(invoice: CanonicalInvoice, positions: set[int], percent: int) -> CanonicalInvoice:
    items = tuple(
        replace(
            item,
            unit_price=(price := money(item.unit_price * (100 + percent) / 100)),
            net=money(item.quantity * price),
        )
        if position in positions
        else item
        for position, item in enumerate(invoice.items)
    )
    return _with_items(invoice, items)


def _iban(country: str, bban: str) -> str:
    """An IBAN with correct check digits (ISO 13616) for any country."""
    digits = "".join(str(int(char, 36)) for char in bban + country + "00")
    return f"{country}{98 - int(digits) % 97:02d}{bban}"


def _wrong_vat(invoice: CanonicalInvoice, variant: str, rng: random.Random) -> CanonicalInvoice:
    if variant == "vat_id_check_digit":
        vat_id = invoice.seller.vat_id
        digit = rng.choice([d for d in "0123456789" if d != vat_id[-1]])
        return replace(invoice, seller=replace(invoice.seller, vat_id=vat_id[:-1] + digit))
    if variant == "vat_id_too_short":
        return replace(invoice, seller=replace(invoice.seller, vat_id=invoice.seller.vat_id[:-1]))
    if variant == "rate_one_line":
        changed = {rng.randrange(len(invoice.items))}
    else:
        rate = rng.choice(sorted({item.vat_rate for item in invoice.items}))
        changed = {i for i, item in enumerate(invoice.items) if item.vat_rate == rate}
    items = tuple(
        replace(item, vat_rate=rng.choice(_OTHER_RATE[item.vat_rate])) if i in changed else item
        for i, item in enumerate(invoice.items)
    )
    return _with_items(invoice, items)


def _swap_digits(amount: Decimal, rng: random.Random) -> Decimal:
    """Two neighbouring digits the wrong way round, as in typing."""
    text = f"{amount:.2f}"
    places = [
        i
        for i in range(len(text) - 1)
        if text[i].isdigit() and text[i + 1].isdigit() and text[i] != text[i + 1]
    ]
    for i in rng.sample(places, len(places)):
        swapped = Decimal(text[:i] + text[i + 1] + text[i] + text[i + 2 :])
        if abs(swapped - amount) > Decimal("0.01"):
            return swapped
    return amount + 9  # every digit the same: no swap changes anything


def _totals(invoice: CanonicalInvoice, variant: str, rng: random.Random) -> CanonicalInvoice:
    delta = Decimal(rng.choice(("0.10", "1.00", "9.00", "-0.05", "100.00", "-0.90")))
    if variant == "two_cents":  # the smallest difference that is not rounding
        delta, variant = Decimal("0.02"), "gross"
    if variant == "gross":
        gross = invoice.gross_total + delta
        return replace(invoice, gross_total=gross, amount_due=gross)
    if variant == "vat_total":
        return replace(invoice, vat_total=invoice.vat_total + delta)
    if variant == "line_amount":
        position = rng.randrange(len(invoice.items))
        items = tuple(
            replace(item, net=item.net + delta) if i == position else item
            for i, item in enumerate(invoice.items)
        )
        return replace(invoice, items=items)
    gross = _swap_digits(invoice.gross_total, rng)
    return replace(invoice, gross_total=gross, amount_due=gross)


def _duplicate(invoice: CanonicalInvoice, variant: str, rng: random.Random) -> CanonicalInvoice:
    if variant == "resent":
        return invoice
    if variant == "number_reformatted":
        number = invoice.number.replace("-", " ").replace("/", "-").lower()
        return replace(invoice, number=number)
    if variant == "number_typo":
        return replace(invoice, number=_typo(invoice.number, rng))
    number = f"{rng.choice(('K', 'ZR', 'B'))}{rng.randint(10000000, 99999999)}"
    if variant == "new_number_same_day":
        return replace(invoice, number=number)
    # Billed again under a new number and a later date: same delivery, same lines.
    later = timedelta(days=rng.randint(3, 20))
    return replace(
        invoice,
        number=number,
        issue_date=invoice.issue_date + later,
        due_date=invoice.due_date + later,
    )


def _price(invoice: CanonicalInvoice, variant: str, rng: random.Random) -> CanonicalInvoice:
    if variant == "all_lines_plus_5_percent":
        return raise_prices(invoice, set(range(len(invoice.items))), 5)
    percent = int(variant.split("_")[1])
    return raise_prices(invoice, {rng.randrange(len(invoice.items))}, percent)


def _changed_iban(invoice: CanonicalInvoice, variant: str, rng: random.Random) -> CanonicalInvoice:
    if variant == "same_bank_other_account":
        bank = invoice.ibans[0][4:12]
        new = _iban("DE", bank + "".join(rng.choice("0123456789") for _ in range(10)))
    elif variant == "foreign_account":
        # Invented bank codes; only the check digits are real arithmetic.
        country, length = rng.choice((("LT", 16), ("NL", 0), ("ES", 20)))
        bban = (
            "XBNK" + "".join(rng.choice("0123456789") for _ in range(10))
            if country == "NL"
            else "000" + "".join(rng.choice("0123456789") for _ in range(length - 3))
        )
        new = _iban(country, bban)
    else:
        new = german_iban(rng)
    if variant == "second_account_added":
        return replace(invoice, ibans=(*invoice.ibans, new))
    return replace(invoice, ibans=(new,))


def _without_order(
    invoice: CanonicalInvoice, variant: str, rng: random.Random, taken: set[str]
) -> CanonicalInvoice:
    if variant == "no_reference":
        return replace(invoice, po_ref=None)
    number = f"PO-{rng.randint(100000, 999999)}"
    while number in taken:
        number = f"PO-{rng.randint(100000, 999999)}"
    return replace(invoice, po_ref=number)


def _one_cent_off(invoice: CanonicalInvoice) -> CanonicalInvoice:
    """The supplier's software rounded the VAT differently by a cent, and
    carried that cent through every total."""
    first, *rest = invoice.tax_groups
    cent = Decimal("0.01")
    return replace(
        invoice,
        tax_groups=(replace(first, tax_amount=first.tax_amount + cent), *rest),
        vat_total=invoice.vat_total + cent,
        gross_total=invoice.gross_total + cent,
        amount_due=invoice.amount_due + cent,
    )


def _price_within_tolerance(
    invoice: CanonicalInvoice, tolerance: Decimal
) -> CanonicalInvoice | None:
    """One line a little dearer than ordered, but within the tolerance; None
    if no price on this invoice can rise by so little."""
    for position, item in enumerate(invoice.items):
        for percent in (1, 2):
            price = money(item.unit_price * (100 + percent) / 100)
            if Decimal(0) < (price - item.unit_price) / item.unit_price <= tolerance:
                return raise_prices(invoice, {position}, percent)
    return None


# --- the set ----------------------------------------------------------------


def build_exception_set(
    world: World, seed: int = DEFAULT_SEED, price_tolerance: Decimal = Decimal("0.02")
) -> ExceptionSet:
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not security
    suppliers = {supplier.id: supplier for supplier in world.suppliers}
    deliveries_of: dict[str, int] = {}
    for delivery in world.deliveries:
        deliveries_of[delivery.po_number] = deliveries_of.get(delivery.po_number, 0) + 1

    pool = list(world.invoices)
    rng.shuffle(pool)

    def take(count: int, fits=lambda entry: True) -> list[WorldInvoice]:
        chosen = [entry for entry in pool if fits(entry)][:count]
        if len(chosen) < count:
            raise ValueError(f"the world has only {len(chosen)} of {count} invoices needed")
        for entry in chosen:
            pool.remove(entry)
        return chosen

    made: list[tuple[str, str, WorldInvoice, CanonicalInvoice]] = []

    def variants(kind: str) -> list[str]:
        cycle = _VARIANTS[kind]
        return [cycle[i % len(cycle)] for i in range(PER_KIND)]

    # Purchases without an order: the order disappears from the ERP, so it
    # must be one that no other invoice belongs to.
    bases = take(PER_KIND, lambda entry: deliveries_of[entry.po_number] == 1)
    removed = {entry.po_number for entry in bases}
    taken = {order.number for order in world.orders}
    for entry, variant in zip(bases, variants(MISSING_PO), strict=True):
        made.append(
            (MISSING_PO, variant, entry, _without_order(entry.invoice, variant, rng, taken))
        )

    # Clean invoices paying into the supplier's second account on file.
    for entry in take(NEAR_MISSES, lambda entry: len(suppliers[entry.supplier_id].ibans) > 1):
        second = suppliers[entry.supplier_id].ibans[1:2]
        made.append((CLEAN, "second_account", entry, replace(entry.invoice, ibans=second)))

    for entry in take(NEAR_MISSES):
        made.append((CLEAN, "one_cent_rounding", entry, _one_cent_off(entry.invoice)))

    within = 0
    for entry in list(pool):
        changed = _price_within_tolerance(entry.invoice, price_tolerance)
        if changed is not None and within < NEAR_MISSES:
            pool.remove(entry)
            made.append((CLEAN, "price_within_tolerance", entry, changed))
            within += 1
    if within < NEAR_MISSES:
        raise ValueError("the world has too few invoices whose price can rise within tolerance")

    def several_lines(entry: WorldInvoice) -> bool:
        return len(entry.invoice.items) > 1

    for kind, change in (
        (WRONG_VAT, _wrong_vat),
        (TOTALS, _totals),
        (PRICE, _price),
        (IBAN, _changed_iban),
    ):
        for variant in variants(kind):
            # A rate can only be wrong on one line of several.
            (entry,) = take(1, several_lines) if variant == "rate_one_line" else take(1)
            made.append((kind, variant, entry, change(entry.invoice, variant, rng)))

    for entry in take(CLEAN_CASES - 3 * NEAR_MISSES):
        made.append((CLEAN, "plain", entry, entry.invoice))

    # Duplicates are copies of invoices that stay on file.
    originals = pool[:PER_KIND]
    if len(originals) < PER_KIND:
        raise ValueError("the world has too few invoices left to duplicate")
    for entry, variant in zip(originals, variants(DUPLICATE), strict=True):
        made.append((DUPLICATE, variant, entry, _duplicate(entry.invoice, variant, rng)))

    # Half of each kind is held out. Variants are dealt in a cycle, so the
    # halves are cut at random, not by position.
    held_out: set[int] = set()
    for kind in (*KINDS, CLEAN):
        members = [position for position, entry in enumerate(made) if entry[0] == kind]
        held_out.update(rng.sample(members, len(members) // 2))
    cases = []
    for position, (kind, variant, entry, invoice) in enumerate(made):
        cases.append(
            ExceptionCase(
                id=f"exc-{position + 1:03d}",
                split="test" if position in held_out else "dev",
                kind=kind,
                variant=variant,
                base_id=entry.id,
                invoice=invoice,
            )
        )

    erp_world = replace(
        world,
        orders=tuple(order for order in world.orders if order.number not in removed),
        deliveries=tuple(d for d in world.deliveries if d.po_number not in removed),
    )
    return ExceptionSet(seed=seed, world=erp_world, on_file=tuple(pool), cases=tuple(cases))


def write_exception_set(exceptions: ExceptionSet, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "cases.jsonl").open("w", encoding="utf-8") as lines:
        for case in exceptions.cases:
            row = asdict(case) | {
                "invoice": json.loads(
                    case.invoice.to_gold(DocumentFormat.UNKNOWN).model_dump_json()
                )
            }
            lines.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=_plain))
            lines.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("eval/out/exceptions"))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    exceptions = build_exception_set(build_world(args.seed), args.seed)
    write_exception_set(exceptions, args.out)
    print(
        f"wrote {args.out}: {len(exceptions.cases)} cases, "
        f"{len(exceptions.on_file)} invoices on file"
    )


if __name__ == "__main__":
    main()
