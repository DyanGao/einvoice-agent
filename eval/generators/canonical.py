"""Canonical synthetic invoices: the single source every rendered format and
its gold record derive from. Everything here is invented data.

A canonical invoice stores its totals explicitly instead of deriving them on
access, so a defect can make them inconsistent on purpose.
"""

import random
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.models import DocumentFormat, Invoice, Line, Party, VatBreakdown

CENT = Decimal("0.01")

XRECHNUNG = "xrechnung"
EN16931 = "en16931"
GUIDELINE_IDS = {
    XRECHNUNG: "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0",
    EN16931: "urn:cen.eu:en16931:2017",
}


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Company:
    name: str
    vat_id: str | None
    street: str
    postcode: str
    city: str
    email: str
    contact_name: str
    phone: str | None


@dataclass(frozen=True)
class Item:
    line_id: str
    name: str
    quantity: Decimal
    unit_code: str
    unit_price: Decimal
    vat_rate: Decimal
    net: Decimal


@dataclass(frozen=True)
class TaxGroup:
    rate: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal


@dataclass(frozen=True)
class CanonicalInvoice:
    number: str
    issue_date: date
    due_date: date | None
    type_code: str
    currency: str
    buyer_reference: str | None
    po_ref: str | None
    seller: Company
    buyer: Company
    ibans: tuple[str, ...]
    items: tuple[Item, ...]
    tax_groups: tuple[TaxGroup, ...]
    line_net_total: Decimal
    net_total: Decimal
    vat_total: Decimal
    gross_total: Decimal
    amount_due: Decimal
    # BT-25: the invoice a credit note corrects.
    preceding_ref: str | None = None

    def to_gold(self, source_format: DocumentFormat) -> Invoice:
        """What a correct reader must extract from any rendering of this invoice."""
        return Invoice(
            number=self.number,
            issue_date=self.issue_date,
            type_code=self.type_code,
            currency=self.currency,
            due_date=self.due_date,
            buyer_reference=self.buyer_reference,
            po_ref=self.po_ref,
            preceding_ref=self.preceding_ref,
            seller=Party(name=self.seller.name, vat_id=self.seller.vat_id),
            buyer=Party(name=self.buyer.name, vat_id=self.buyer.vat_id),
            payment_ibans=self.ibans,
            lines=tuple(
                Line(
                    line_id=item.line_id,
                    description=item.name,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    net=item.net,
                    vat_rate=item.vat_rate,
                )
                for item in self.items
            ),
            vat_breakdown=tuple(
                VatBreakdown(
                    category="S",
                    rate=group.rate,
                    taxable_amount=group.taxable_amount,
                    tax_amount=group.tax_amount,
                )
                for group in self.tax_groups
            ),
            line_net_total=self.line_net_total,
            net_total=self.net_total,
            vat_total=self.vat_total,
            gross_total=self.gross_total,
            amount_due=self.amount_due,
            source_format=source_format,
        )


@dataclass(frozen=True)
class Defect:
    """A deliberate rule violation and the rule the validator must report."""

    name: str
    rule_id: str
    # BR-DE-* rules belong to XRechnung; a plain EN 16931 invoice may break them.
    xrechnung_only: bool = False

    def applies_to(self, profile: str) -> bool:
        return profile == XRECHNUNG or not self.xrechnung_only

    def apply(self, invoice: CanonicalInvoice) -> CanonicalInvoice:
        return _DEFECT_MUTATIONS[self.name](invoice)


def _bump_first_tax_group(invoice: CanonicalInvoice) -> CanonicalInvoice:
    first, *rest = invoice.tax_groups
    return replace(invoice, tax_groups=(replace(first, tax_amount=first.tax_amount + 1), *rest))


_DEFECT_MUTATIONS = {
    "missing_buyer_reference": lambda inv: replace(inv, buyer_reference=None),
    "missing_seller_phone": lambda inv: replace(inv, seller=replace(inv.seller, phone=None)),
    "gross_total_off": lambda inv: replace(inv, gross_total=inv.gross_total + 1),
    "line_sum_off": lambda inv: replace(inv, line_net_total=inv.line_net_total + 1),
    "vat_amount_off": _bump_first_tax_group,
}

DEFECTS = (
    Defect("missing_buyer_reference", "BR-DE-15", xrechnung_only=True),
    Defect("missing_seller_phone", "BR-DE-6", xrechnung_only=True),
    Defect("gross_total_off", "BR-CO-15"),
    Defect("line_sum_off", "BR-CO-10"),
    # BR-CO-14 (VAT total = sum of category amounts) is reported for both syntaxes;
    # BR-CO-17 only fires in the UBL rule set of this validator configuration.
    Defect("vat_amount_off", "BR-CO-14"),
)

_SELLERS = (
    "Nordlicht Bürobedarf GmbH",
    "Müller & Söhne Elektrotechnik KG",
    "Rheintal Logistik AG",
    "Grünwald Gartenbau e.K.",
    "Hansa Werkzeughandel GmbH & Co. KG",
    "Süddeutsche Papierwerke GmbH",
    "Böttcher IT-Service UG (haftungsbeschränkt)",
    "Alpenblick Catering GmbH",
)
_BUYERS = (
    "Stadtwerke Beispielstadt GmbH",
    "Praxis Dr. Äpfelmann",
    "Schreinerei Holzwurm OHG",
    "Köhler <Maschinenbau> AG",
    "Bäckerei Sonnenschein",
    "Ingenieurbüro Weiß + Partner",
)
_CITIES = (
    ("10115", "Berlin"),
    ("20095", "Hamburg"),
    ("80331", "München"),
    ("50667", "Köln"),
    ("60311", "Frankfurt am Main"),
    ("70173", "Stuttgart"),
    ("04109", "Leipzig"),
    ("90402", "Nürnberg"),
)
_STREETS = ("Hauptstraße", "Bahnhofstraße", "Gartenweg", "Industriestraße", "Am Mühlbach")
_CONTACTS = ("Anna Schmidt", "Jürgen Weber", "Sabine Krüger", "Tobias Groß", "Elif Yılmaz")
_ITEMS = (
    ("Kopierpapier A4, 500 Blatt", "C62"),
    ("Beratung vor Ort", "HUR"),
    ("Schrauben M8 × 40, verzinkt", "C62"),
    ("Wartungspauschale", "C62"),
    ("Kabel NYM-J 3x1,5 mm²", "MTR"),
    ("Druckerpatrone schwarz", "C62"),
    ("Anfahrt", "KMT"),
    ("Blumenerde 40 l", "C62"),
    ("Software-Lizenz, 1 Jahr", "C62"),
    ("Brötchen & Gebäck", "KGM"),
)
_BUYER_REFERENCES = ("04011000-12345-03", "991-33333TEST-33", "90000000-03083-72")
_VAT_RATES = (Decimal("19"), Decimal("19"), Decimal("19"), Decimal("7"))


def german_iban(rng: random.Random) -> str:
    """A syntactically valid German IBAN with a correct check digit; the bank
    code starts with 000 so it cannot belong to a real bank."""
    bban = "000" + "".join(rng.choice("0123456789") for _ in range(15))
    check = 98 - int(bban + "131400") % 97
    return f"DE{check:02d}{bban}"


def _vat_id(rng: random.Random) -> str:
    return "DE" + "".join(rng.choice("0123456789") for _ in range(9))


def _company(rng: random.Random, name: str, *, with_vat_id: bool) -> Company:
    postcode, city = rng.choice(_CITIES)
    slug = "".join(ch for ch in name.lower() if ch.isascii() and ch.isalpha())[:12]
    return Company(
        name=name,
        vat_id=_vat_id(rng) if with_vat_id else None,
        street=f"{rng.choice(_STREETS)} {rng.randint(1, 120)}",
        postcode=postcode,
        city=city,
        email=f"rechnung@{slug}.example",
        contact_name=rng.choice(_CONTACTS),
        phone=f"+49 {rng.randint(30, 9999)} {rng.randint(100000, 999999)}",
    )


def _item(rng: random.Random, position: int) -> Item:
    name, unit_code = rng.choice(_ITEMS)
    whole_units = unit_code == "C62"
    quantity = Decimal(rng.randint(1, 40)) if whole_units else Decimal(rng.randint(25, 4000)) / 100
    unit_price = Decimal(rng.randint(50, 250000)) / 100
    return Item(
        line_id=str(position),
        name=name,
        quantity=quantity,
        unit_code=unit_code,
        unit_price=unit_price,
        vat_rate=rng.choice(_VAT_RATES),
        net=money(quantity * unit_price),
    )


def build_invoice(seed: int, index: int) -> CanonicalInvoice:
    """Invoice number `index` of the set `seed`; independent of all others."""
    rng = random.Random(f"{seed}:{index}")  # noqa: S311 - reproducible test data, not security
    issue_date = date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))
    items = tuple(_item(rng, position) for position in range(1, rng.randint(1, 6) + 1))

    tax_groups = []
    for rate in sorted({item.vat_rate for item in items}, reverse=True):
        taxable = sum((item.net for item in items if item.vat_rate == rate), Decimal(0))
        tax_groups.append(
            TaxGroup(rate=rate, taxable_amount=taxable, tax_amount=money(taxable * rate / 100))
        )
    net_total = sum((item.net for item in items), Decimal(0))
    vat_total = sum((group.tax_amount for group in tax_groups), Decimal(0))

    return CanonicalInvoice(
        number=f"RE-{issue_date.year}-{rng.randint(1, 99999):05d}",
        issue_date=issue_date,
        due_date=issue_date + timedelta(days=rng.choice((7, 14, 30)))
        if rng.random() < 0.7
        else None,
        type_code="380",
        currency="EUR",
        buyer_reference=rng.choice(_BUYER_REFERENCES),
        po_ref=f"PO-{rng.randint(100000, 999999)}" if rng.random() < 0.6 else None,
        seller=_company(rng, rng.choice(_SELLERS), with_vat_id=True),
        buyer=_company(rng, rng.choice(_BUYERS), with_vat_id=rng.random() < 0.5),
        ibans=tuple(german_iban(rng) for _ in range(2 if rng.random() < 0.15 else 1)),
        items=items,
        tax_groups=tuple(tax_groups),
        line_net_total=net_total,
        net_total=net_total,
        vat_total=vat_total,
        gross_total=net_total + vat_total,
        amount_due=net_total + vat_total,
    )


def _totals_of(items: tuple[Item, ...]) -> dict:
    """Tax groups and totals for these items, as build_invoice makes them."""
    groups = []
    for rate in sorted({item.vat_rate for item in items}, reverse=True):
        taxable = sum((item.net for item in items if item.vat_rate == rate), Decimal(0))
        groups.append(
            TaxGroup(rate=rate, taxable_amount=taxable, tax_amount=money(taxable * rate / 100))
        )
    net = sum((item.net for item in items), Decimal(0))
    vat = sum((group.tax_amount for group in groups), Decimal(0))
    return {
        "items": items,
        "tax_groups": tuple(groups),
        "line_net_total": net,
        "net_total": net,
        "vat_total": vat,
        "gross_total": net + vat,
        "amount_due": net + vat,
    }


def credit_note_for(
    invoice: CanonicalInvoice,
    number: str,
    days_later: int = 10,
    *,
    items: tuple[Item, ...] | None = None,
    names_invoice: bool = True,
) -> CanonicalInvoice:
    """A credit note (381) for the invoice: all of it, or only `items` (some of
    its lines, perhaps fewer of them). It names the invoice (BT-25) unless
    `names_invoice` is False. Its amounts are positive, as XRechnung wants
    them: the type says it is a credit."""
    changes = {} if items is None else _totals_of(items)
    return replace(
        invoice,
        number=number,
        issue_date=invoice.issue_date + timedelta(days=days_later),
        due_date=None,
        po_ref=None,
        type_code="381",
        preceding_ref=invoice.number if names_invoice else None,
        **changes,
    )
