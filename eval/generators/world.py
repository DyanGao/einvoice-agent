"""The matching world: a small synthetic company's purchasing data.

Suppliers, purchase orders, deliveries, the invoices that followed and the
bank transactions that paid them — with the correct links between them kept
on the side as ground truth. Everything is invented.

The world is what the mock ERP is seeded with and what matching is measured
on. It contains the cases that make matching hard: order references with
typos or missing altogether, orders delivered and invoiced in two parts,
invoices paid in two instalments, one payment covering three invoices,
payment references with typos or without an invoice number, and payments and
open orders that belong to no invoice at all.

    python -m eval.generators.world --out eval/out/world
"""

import argparse
import json
import random
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.models import DocumentFormat
from eval.generators.canonical import (
    CanonicalInvoice,
    Company,
    Item,
    TaxGroup,
    german_iban,
    money,
)

DEFAULT_SEED = 20261113
SUPPLIERS = 50
ORDERS = 400
BANK_TRANSACTIONS = 600

# How an invoice refers to its order, and how it was paid.
PO_EXACT, PO_TYPO, PO_MISSING = "exact", "typo", "missing"
PAID_ONCE, PAID_IN_PARTS, PAID_COMBINED, UNPAID = "single", "partial", "combined", "unpaid"
# How a payment's reference text names the invoice.
REF_EXACT, REF_TYPO, REF_NONE = "exact", "typo", "none"


@dataclass(frozen=True)
class Supplier:
    id: str
    company: Company
    ibans: tuple[str, ...]


@dataclass(frozen=True)
class OrderLine:
    position: int
    description: str
    quantity: Decimal
    unit_code: str
    unit_price: Decimal
    vat_rate: Decimal


@dataclass(frozen=True)
class PurchaseOrder:
    number: str
    supplier_id: str
    order_date: date
    lines: tuple[OrderLine, ...]


@dataclass(frozen=True)
class DeliveryLine:
    po_position: int
    quantity: Decimal


@dataclass(frozen=True)
class Delivery:
    id: str
    po_number: str
    delivery_date: date
    lines: tuple[DeliveryLine, ...]


@dataclass(frozen=True)
class BankTransaction:
    """Money leaving our account. `amount` is positive."""

    id: str
    booking_date: date
    amount: Decimal
    counterparty_name: str
    counterparty_iban: str
    purpose: str


@dataclass(frozen=True)
class Payment:
    bank_transaction_id: str
    amount: Decimal  # the part of that transaction that pays this invoice


@dataclass(frozen=True)
class WorldInvoice:
    id: str
    split: str  # dev | test
    supplier_id: str
    invoice: CanonicalInvoice
    # Ground truth: what a correct matcher must find.
    po_number: str
    delivery_id: str
    payments: tuple[Payment, ...]
    # Which hard case this invoice is, for breaking results down.
    po_ref_kind: str
    payment_kind: str
    payment_ref_kind: str | None


@dataclass(frozen=True)
class World:
    seed: int
    buyer: Company
    suppliers: tuple[Supplier, ...]
    orders: tuple[PurchaseOrder, ...]
    deliveries: tuple[Delivery, ...]
    invoices: tuple[WorldInvoice, ...]
    bank_transactions: tuple[BankTransaction, ...]


# --- vocabulary -----------------------------------------------------------

_NAMES = (
    "Albrecht", "Bergmann", "Clausen", "Dietrich", "Engelhardt", "Falkenberg", "Gerlach",
    "Hartmann", "Imhoff", "Jansen", "Kessler", "Lindner", "Morgenstern", "Neumann", "Ohlsen",
    "Petersen", "Quante", "Reinhardt", "Sommerfeld", "Thalmann", "Ullrich", "Vogelsang",
    "Wiesner", "Zeller", "Brandt",
)  # fmt: skip
_TRADES = (
    "Bürobedarf", "Elektrotechnik", "Logistik", "Gartenbau", "Werkzeughandel", "Papierwerke",
    "IT-Service", "Catering", "Gebäudereinigung", "Verpackungen", "Metallbau", "Drucktechnik",
)  # fmt: skip
_LEGAL_FORMS = ("GmbH", "GmbH & Co. KG", "AG", "KG", "e.K.", "OHG", "UG (haftungsbeschränkt)")
_CITIES = (
    ("10115", "Berlin"), ("20095", "Hamburg"), ("80331", "München"), ("50667", "Köln"),
    ("60311", "Frankfurt am Main"), ("70173", "Stuttgart"), ("04109", "Leipzig"),
    ("90402", "Nürnberg"), ("28195", "Bremen"), ("01067", "Dresden"),
)  # fmt: skip
_STREETS = ("Hauptstraße", "Bahnhofstraße", "Gartenweg", "Industriestraße", "Am Mühlbach")
_CONTACTS = ("Anna Schmidt", "Jürgen Weber", "Sabine Krüger", "Tobias Groß", "Elif Yılmaz")
# (description, unit code, typical unit price, VAT rate)
_CATALOGUE = (
    ("Kopierpapier A4, 500 Blatt", "C62", "4.90", "19"),
    ("Druckerpatrone schwarz", "C62", "38.50", "19"),
    ("Ordner A4, 8 cm", "C62", "2.35", "19"),
    ("Schrauben M8 × 40, verzinkt, 100 Stück", "C62", "12.80", "19"),
    ("Kabel NYM-J 3x1,5 mm²", "MTR", "0.89", "19"),
    ("Akkuschrauber 18 V", "C62", "149.00", "19"),
    ("Beratung vor Ort", "HUR", "95.00", "19"),
    ("Wartungspauschale", "C62", "320.00", "19"),
    ("Software-Lizenz, 1 Jahr", "C62", "240.00", "19"),
    ("Transport Palette", "C62", "68.00", "19"),
    ("Anfahrt", "KMT", "0.52", "19"),
    ("Blumenerde 40 l", "C62", "7.45", "19"),
    ("Unterhaltsreinigung", "HUR", "27.50", "19"),
    ("Versandkarton 400 × 300 × 200", "C62", "1.15", "19"),
    ("Brötchen & Gebäck", "KGM", "9.80", "7"),
    ("Mittagsbuffet pro Person", "C62", "18.50", "7"),
    ("Fachbuch Rechnungswesen", "C62", "49.90", "7"),
    ("Stahlprofil 40 × 40", "MTR", "14.20", "19"),
)
_NOISE_COUNTERPARTIES = (
    ("Finanzamt Beispielstadt", "Umsatzsteuer-Vorauszahlung"),
    ("Hausverwaltung Sonnenhof GbR", "Miete Büro"),
    ("Stadtwerke Beispielstadt GmbH", "Abschlag Strom"),
    ("Telekommunikation Nord AG", "Rechnung Mobilfunk"),
    ("Lohn und Gehalt", "Gehalt"),
    ("Versicherung Allgemein AG", "Beitrag Betriebshaftpflicht"),
)
# XRechnung requires a buyer reference (rule BR-DE-15); ours asks suppliers to quote this one.
BUYER_REFERENCE = "04011000-12345-03"
BUYER = Company(
    name="Nordlicht Handel GmbH",
    vat_id="DE123456788",
    street="Speicherstraße 12",
    postcode="20095",
    city="Hamburg",
    email="rechnungseingang@nordlicht-handel.example",
    contact_name="Sabine Krüger",
    phone="+49 40 555010",
)


def german_vat_id(rng: random.Random) -> str:
    """A German VAT ID with a correct check digit (ISO 7064 MOD 11,10)."""
    digits = [rng.randint(1, 9)] + [rng.randint(0, 9) for _ in range(7)]
    product = 10
    for digit in digits:
        total = (digit + product) % 10 or 10
        product = (2 * total) % 11
    return "DE" + "".join(map(str, digits)) + str((11 - product) % 10)


# --- building blocks ------------------------------------------------------


def _suppliers(rng: random.Random) -> tuple[Supplier, ...]:
    names: list[str] = []
    while len(names) < SUPPLIERS:
        name = f"{rng.choice(_NAMES)} {rng.choice(_TRADES)} {rng.choice(_LEGAL_FORMS)}"
        if name not in names:
            names.append(name)
    suppliers = []
    for number, name in enumerate(names, start=1):
        postcode, city = rng.choice(_CITIES)
        slug = "".join(ch for ch in name.lower() if ch.isascii() and ch.isalpha())[:14]
        company = Company(
            name=name,
            vat_id=german_vat_id(rng),
            street=f"{rng.choice(_STREETS)} {rng.randint(1, 120)}",
            postcode=postcode,
            city=city,
            email=f"rechnung@{slug}.example",
            contact_name=rng.choice(_CONTACTS),
            phone=f"+49 {rng.randint(30, 9999)} {rng.randint(100000, 999999)}",
        )
        ibans = tuple(german_iban(rng) for _ in range(2 if rng.random() < 0.1 else 1))
        suppliers.append(Supplier(id=f"S{number:03d}", company=company, ibans=ibans))
    return tuple(suppliers)


def _order_lines(rng: random.Random) -> tuple[OrderLine, ...]:
    lines = []
    for position, (name, unit, price, rate) in enumerate(
        rng.sample(_CATALOGUE, rng.randint(1, 5)), start=1
    ):
        whole = unit == "C62"
        quantity = Decimal(rng.randint(1, 60)) if whole else Decimal(rng.randint(4, 400)) / 4
        # Each order negotiates its own price around the catalogue price.
        unit_price = money(Decimal(price) * Decimal(rng.randint(90, 110)) / 100)
        lines.append(OrderLine(position, name, quantity, unit, unit_price, Decimal(rate)))
    return tuple(lines)


def _orders(rng: random.Random, suppliers: tuple[Supplier, ...]) -> tuple[PurchaseOrder, ...]:
    numbers = rng.sample(range(100000, 1000000), ORDERS)
    return tuple(
        PurchaseOrder(
            number=f"PO-{number}",
            supplier_id=rng.choice(suppliers).id,
            order_date=date(2026, 1, 5) + timedelta(days=rng.randint(0, 230)),
            lines=_order_lines(rng),
        )
        for number in numbers
    )


def _split(order: PurchaseOrder, rng: random.Random) -> list[tuple[DeliveryLine, ...]]:
    """An order's lines as one delivery or, sometimes, two."""
    everything = tuple(DeliveryLine(line.position, line.quantity) for line in order.lines)
    if rng.random() >= 0.15:
        return [everything]
    if len(order.lines) > 1:
        cut = rng.randint(1, len(order.lines) - 1)
        return [everything[:cut], everything[cut:]]
    (line,) = order.lines
    first = (line.quantity / 2).quantize(
        Decimal("1") if line.unit_code == "C62" else Decimal("0.01")
    )
    if first <= 0 or first >= line.quantity:
        return [everything]
    return [
        (DeliveryLine(line.position, first),),
        (DeliveryLine(line.position, line.quantity - first),),
    ]


def _typo(text: str, rng: random.Random) -> str:
    """One slip of the kind people and OCR make in a reference number."""
    digits = [index for index, char in enumerate(text) if char.isdigit()]
    kind = rng.choice(("swap", "drop", "replace", "letter"))
    if kind == "letter" and "0" in text:
        return text.replace("0", "O", 1)
    index = rng.choice(digits[:-1])
    if kind == "swap" and text[index] != text[index + 1] and text[index + 1].isdigit():
        return text[:index] + text[index + 1] + text[index] + text[index + 2 :]
    if kind == "drop":
        return text[:index] + text[index + 1 :]
    replacement = rng.choice([d for d in "0123456789" if d != text[index]])
    return text[:index] + replacement + text[index + 1 :]


def _invoice_number(style: int, year: int, serial: int) -> str:
    return (
        f"RE-{year}-{serial:05d}",
        f"{year}/{serial:04d}",
        f"R{year % 100}{serial:06d}",
        f"INV-{serial:06d}",
    )[style]


def _canonical(
    number: str,
    issued: date,
    po_ref: str | None,
    supplier: Supplier,
    items: tuple[Item, ...],
    rng: random.Random,
) -> CanonicalInvoice:
    groups = []
    for rate in sorted({item.vat_rate for item in items}, reverse=True):
        taxable = sum((item.net for item in items if item.vat_rate == rate), Decimal(0))
        groups.append(TaxGroup(rate, taxable, money(taxable * rate / 100)))
    net = sum((item.net for item in items), Decimal(0))
    vat = sum((group.tax_amount for group in groups), Decimal(0))
    return CanonicalInvoice(
        number=number,
        issue_date=issued,
        due_date=issued + timedelta(days=rng.choice((14, 30))),
        type_code="380",
        currency="EUR",
        buyer_reference=BUYER_REFERENCE,
        po_ref=po_ref,
        seller=supplier.company,
        buyer=BUYER,
        ibans=supplier.ibans[:1],
        items=items,
        tax_groups=tuple(groups),
        line_net_total=net,
        net_total=net,
        vat_total=vat,
        gross_total=net + vat,
        amount_due=net + vat,
    )


# --- the world ------------------------------------------------------------


@dataclass
class _Draft:
    """An invoice before its payments are known."""

    supplier: Supplier
    order: PurchaseOrder
    delivery: Delivery
    invoice: CanonicalInvoice
    po_ref_kind: str


def _deliveries_and_invoices(
    rng: random.Random, suppliers: tuple[Supplier, ...], orders: tuple[PurchaseOrder, ...]
) -> tuple[list[Delivery], list[_Draft]]:
    by_id = {supplier.id: supplier for supplier in suppliers}
    order_numbers = {order.number for order in orders}
    styles = {supplier.id: rng.randrange(4) for supplier in suppliers}
    serials = {supplier.id: rng.randint(100, 5000) for supplier in suppliers}
    deliveries: list[Delivery] = []
    drafts: list[_Draft] = []
    for order in orders:
        if rng.random() < 0.1:
            continue  # ordered, nothing delivered or invoiced yet
        supplier = by_id[order.supplier_id]
        lines_by_position = {line.position: line for line in order.lines}
        delivered_on = order.order_date + timedelta(days=rng.randint(3, 20))
        for part in _split(order, rng):
            delivery = Delivery(
                id=f"LS-{len(deliveries) + 1:06d}",
                po_number=order.number,
                delivery_date=delivered_on,
                lines=part,
            )
            deliveries.append(delivery)
            items = tuple(
                Item(
                    line_id=str(position),
                    name=lines_by_position[line.po_position].description,
                    quantity=line.quantity,
                    unit_code=lines_by_position[line.po_position].unit_code,
                    unit_price=lines_by_position[line.po_position].unit_price,
                    vat_rate=lines_by_position[line.po_position].vat_rate,
                    net=money(line.quantity * lines_by_position[line.po_position].unit_price),
                )
                for position, line in enumerate(part, start=1)
            )
            roll = rng.random()
            if roll < 0.6:
                kind, po_ref = PO_EXACT, order.number
            elif roll < 0.7:
                kind, po_ref = PO_TYPO, _typo(order.number, rng)
                while po_ref in order_numbers:  # a typo must not name another real order
                    po_ref = _typo(order.number, rng)
            else:
                kind, po_ref = PO_MISSING, None
            issued = delivered_on + timedelta(days=rng.randint(0, 5))
            serials[supplier.id] += rng.randint(1, 9)
            number = _invoice_number(styles[supplier.id], issued.year, serials[supplier.id])
            drafts.append(
                _Draft(
                    supplier,
                    order,
                    delivery,
                    _canonical(number, issued, po_ref, supplier, items, rng),
                    kind,
                )
            )
            delivered_on += timedelta(days=rng.randint(5, 15))
    return deliveries, drafts


def _bank_name(name: str, rng: random.Random) -> str:
    """Banks shorten and upper-case names as they please."""
    return name.upper()[:27] if rng.random() < 0.3 else name


def _reference(numbers: list[str], kind: str, supplier: Supplier, rng: random.Random) -> str:
    if kind == REF_NONE:
        return rng.choice(("Zahlung", "Überweisung", "Ausgleich offener Posten"))
    if kind == REF_TYPO:
        numbers = [_typo(number, rng) for number in numbers]
    if len(numbers) > 1:
        return rng.choice(("Sammelzahlung ", "")) + rng.choice((", ", " / ", " ")).join(numbers)
    template = rng.choice(("{n}", "Rechnung {n}", "RG {n} KD-NR {c}", "Rechnungsnr. {n}"))
    return template.format(n=numbers[0], c=rng.randint(10000, 99999))


def _payments(
    rng: random.Random, drafts: list[_Draft]
) -> tuple[list[dict], dict[int, tuple[str, str | None, list[tuple[int, Decimal]]]]]:
    """Unnumbered transactions, and per invoice (by position in `drafts`) how
    it was paid: kind, reference kind, and (transaction position, amount)."""
    transactions: list[dict] = []
    paid: dict[int, tuple[str, str | None, list[tuple[int, Decimal]]]] = {}

    def pay(supplier: Supplier, amount: Decimal, on: date, numbers: list[str], kind: str) -> int:
        transactions.append(
            {
                "booking_date": on,
                "amount": amount,
                "counterparty_name": _bank_name(supplier.company.name, rng),
                "counterparty_iban": supplier.ibans[0],
                "purpose": _reference(numbers, kind, supplier, rng),
            }
        )
        return len(transactions) - 1

    def ref_kind() -> str:
        roll = rng.random()
        return REF_EXACT if roll < 0.75 else REF_TYPO if roll < 0.9 else REF_NONE

    def due(draft: _Draft) -> date:
        return draft.invoice.due_date - timedelta(days=rng.randint(0, 10))

    open_by_supplier: dict[str, list[int]] = {}
    for index, draft in enumerate(drafts):
        roll = rng.random()
        if roll < 0.2:
            paid[index] = (UNPAID, None, [])
        elif roll < 0.45:
            open_by_supplier.setdefault(draft.supplier.id, []).append(index)  # maybe combined
        elif roll < 0.55:
            kind = ref_kind()
            total = draft.invoice.gross_total
            first = money(total * Decimal(rng.choice((30, 50, 60))) / 100)
            parts = [
                (pay(draft.supplier, first, due(draft), [draft.invoice.number], kind), first),
                (
                    pay(
                        draft.supplier,
                        total - first,
                        due(draft) + timedelta(days=rng.randint(7, 30)),
                        [draft.invoice.number],
                        kind,
                    ),
                    total - first,
                ),
            ]
            paid[index] = (PAID_IN_PARTS, kind, parts)
        else:
            kind = ref_kind()
            total = draft.invoice.gross_total
            position = pay(draft.supplier, total, due(draft), [draft.invoice.number], kind)
            paid[index] = (PAID_ONCE, kind, [(position, total)])

    # One transfer for three invoices of the same supplier; leftovers are paid singly.
    for indexes in open_by_supplier.values():
        while len(indexes) >= 3:
            group, indexes = indexes[:3], indexes[3:]
            members = [drafts[index] for index in group]
            kind = REF_EXACT if rng.random() < 0.8 else REF_TYPO
            total = sum((member.invoice.gross_total for member in members), Decimal(0))
            on = max(member.invoice.due_date for member in members)
            position = pay(
                members[0].supplier, total, on, [member.invoice.number for member in members], kind
            )
            for index, member in zip(group, members, strict=True):
                paid[index] = (PAID_COMBINED, kind, [(position, member.invoice.gross_total)])
        for index in indexes:
            draft = drafts[index]
            kind = ref_kind()
            total = draft.invoice.gross_total
            position = pay(draft.supplier, total, due(draft), [draft.invoice.number], kind)
            paid[index] = (PAID_ONCE, kind, [(position, total)])
    return transactions, paid


def _noise(rng: random.Random, suppliers: tuple[Supplier, ...], count: int) -> list[dict]:
    """Payments that settle no invoice in the world."""
    noise = []
    for _ in range(count):
        on = date(2026, 1, 5) + timedelta(days=rng.randint(0, 300))
        if rng.random() < 0.3:
            # To a real supplier, but for nothing we hold an invoice for.
            supplier = rng.choice(suppliers)
            name, iban = _bank_name(supplier.company.name, rng), supplier.ibans[0]
            purpose = rng.choice(("Anzahlung", "Vorauszahlung Auftrag", "Abschlag"))
            amount = Decimal(rng.randint(1, 80) * 50)
        else:
            name, purpose = rng.choice(_NOISE_COUNTERPARTIES)
            iban = german_iban(rng)
            purpose = f"{purpose} {on:%m/%Y}"
            amount = Decimal(rng.randint(2000, 900000)) / 100
        noise.append(
            {
                "booking_date": on,
                "amount": amount,
                "counterparty_name": name,
                "counterparty_iban": iban,
                "purpose": purpose,
            }
        )
    return noise


def build_world(seed: int = DEFAULT_SEED) -> World:
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not security
    suppliers = _suppliers(rng)
    orders = _orders(rng, suppliers)
    deliveries, drafts = _deliveries_and_invoices(rng, suppliers, orders)
    raw, paid = _payments(rng, drafts)
    if len(raw) > BANK_TRANSACTIONS:
        raise ValueError(f"{len(raw)} payments do not fit into {BANK_TRANSACTIONS} transactions")
    raw += _noise(rng, suppliers, BANK_TRANSACTIONS - len(raw))

    # Number the transactions by date, so an id says nothing about what it pays.
    order_of = sorted(
        range(len(raw)), key=lambda position: (raw[position]["booking_date"], position)
    )
    ids = {position: f"TX-{rank:06d}" for rank, position in enumerate(order_of, start=1)}
    transactions = tuple(
        BankTransaction(id=ids[position], **raw[position]) for position in order_of
    )

    invoices = []
    for index, draft in enumerate(drafts):
        payment_kind, reference_kind, parts = paid[index]
        invoices.append(
            WorldInvoice(
                id=f"inv-{index + 1:04d}",
                split="test" if rng.random() < 0.3 else "dev",
                supplier_id=draft.supplier.id,
                invoice=draft.invoice,
                po_number=draft.order.number,
                delivery_id=draft.delivery.id,
                payments=tuple(Payment(ids[position], amount) for position, amount in parts),
                po_ref_kind=draft.po_ref_kind,
                payment_kind=payment_kind,
                payment_ref_kind=reference_kind,
            )
        )
    return World(
        seed=seed,
        buyer=BUYER,
        suppliers=suppliers,
        orders=orders,
        deliveries=tuple(deliveries),
        invoices=tuple(invoices),
        bank_transactions=transactions,
    )


# --- files ----------------------------------------------------------------


def _plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(type(value))


def write_world(world: World, out_dir: Path) -> None:
    """The ERP's data, and next to it — never in the ERP — the ground truth."""
    out_dir.mkdir(parents=True, exist_ok=True)

    def dump(name: str, rows: list[dict]) -> None:
        with (out_dir / name).open("w", encoding="utf-8") as lines:
            for row in rows:
                lines.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=_plain))
                lines.write("\n")

    dump("suppliers.jsonl", [asdict(supplier) for supplier in world.suppliers])
    dump("orders.jsonl", [asdict(order) for order in world.orders])
    dump("deliveries.jsonl", [asdict(delivery) for delivery in world.deliveries])
    dump("bank_transactions.jsonl", [asdict(tx) for tx in world.bank_transactions])
    dump(
        "gold.jsonl",
        [
            {
                "id": entry.id,
                "split": entry.split,
                "supplier_id": entry.supplier_id,
                # The world's invoices are data, not files: no format to speak of.
                "invoice": json.loads(
                    entry.invoice.to_gold(DocumentFormat.UNKNOWN).model_dump_json()
                ),
                "match": {
                    "po_number": entry.po_number,
                    "delivery_id": entry.delivery_id,
                    "payments": [asdict(payment) for payment in entry.payments],
                },
                "po_ref_kind": entry.po_ref_kind,
                "payment_kind": entry.payment_kind,
                "payment_ref_kind": entry.payment_ref_kind,
            }
            for entry in world.invoices
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("eval/out/world"))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    world = build_world(args.seed)
    write_world(world, args.out)
    print(
        f"wrote {args.out}: {len(world.suppliers)} suppliers, {len(world.orders)} orders, "
        f"{len(world.deliveries)} deliveries, {len(world.invoices)} invoices, "
        f"{len(world.bank_transactions)} bank transactions"
    )


if __name__ == "__main__":
    main()
