"""Layout variants for the synthetic PDF set.

A layout fixes language, wording, number and date formats, and where the
blocks sit on the page. The twelve below are meant to differ in the ways real
supplier invoices differ, so an extractor cannot pass by learning one template.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

_MONTHS = {
    "de": [
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ],
    "en": [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
}

# One entry per wording variant; a layout picks a variant index.
LABELS = {
    "de": {
        "title": ("Rechnung", "RECHNUNG", "Rechnung"),
        "number": ("Rechnungsnummer", "Rechnung Nr.", "Beleg-Nr."),
        "issue_date": ("Rechnungsdatum", "Datum", "Belegdatum"),
        "due_date": ("Zahlbar bis", "Fällig am", "Zahlungsziel"),
        "no_due_date": ("Zahlbar sofort ohne Abzug.",) * 3,
        "po_ref": ("Bestellnummer", "Ihre Bestellung", "Bestell-Nr."),
        "buyer_reference": ("Leitweg-ID", "Ihr Zeichen", "Käuferreferenz"),
        "seller_vat_id": ("USt-IdNr.", "Umsatzsteuer-ID", "UID"),
        "buyer_vat_id": ("Ihre USt-IdNr.", "USt-IdNr. Kunde", "Kunden-UID"),
        "pos": ("Pos.", "Nr.", "#"),
        "desc": ("Bezeichnung", "Artikel", "Leistung"),
        "qty": ("Menge", "Anzahl", "Menge"),
        "unit_price": ("Einzelpreis", "E-Preis", "Preis/Einheit"),
        "vat": ("USt.", "MwSt.", "USt %"),
        "net": ("Gesamt", "Betrag", "Netto"),
        "net_total": ("Nettobetrag", "Zwischensumme", "Summe netto"),
        "vat_line": (
            "zzgl. {rate} % USt. auf {base}",
            "{rate} % MwSt. von {base}",
            "USt. {rate} % ({base})",
        ),
        "gross_total": ("Gesamtbetrag", "Rechnungsbetrag", "Bruttobetrag"),
        "amount_due": ("Zu zahlen", "Zahlbetrag", "Offener Betrag"),
        "iban": ("IBAN", "Bankverbindung IBAN", "Konto (IBAN)"),
    },
    "en": {
        "title": ("Invoice", "INVOICE", "Tax Invoice"),
        "number": ("Invoice number", "Invoice no.", "Invoice #"),
        "issue_date": ("Invoice date", "Date", "Date of issue"),
        "due_date": ("Due date", "Payment due", "Pay by"),
        "no_due_date": ("Payable immediately without deduction.",) * 3,
        "po_ref": ("PO number", "Your order", "Purchase order"),
        "buyer_reference": ("Buyer reference", "Your reference", "Routing ID"),
        "seller_vat_id": ("VAT ID", "VAT Reg. No.", "VAT number"),
        "buyer_vat_id": ("Customer VAT ID", "Your VAT ID", "Buyer VAT number"),
        "pos": ("Item", "No.", "#"),
        "desc": ("Description", "Product", "Service"),
        "qty": ("Qty", "Quantity", "Units"),
        "unit_price": ("Unit price", "Price", "Rate"),
        "vat": ("VAT", "VAT %", "Tax"),
        "net": ("Amount", "Net", "Line total"),
        "net_total": ("Net amount", "Subtotal", "Total excl. VAT"),
        "vat_line": (
            "VAT {rate} % on {base}",
            "plus {rate} % VAT of {base}",
            "Tax {rate} % ({base})",
        ),
        "gross_total": ("Total", "Invoice total", "Total incl. VAT"),
        "amount_due": ("Amount due", "Balance due", "To pay"),
        "iban": ("IBAN", "Bank details IBAN", "Account (IBAN)"),
    },
}

# Wording used only by the held-out layouts (wording index 3). None of these
# labels occurs in a dev layout.
HELD_OUT_WORDING = 3
HELD_OUT_LABELS = {
    "de": {
        "title": "Faktura",
        "number": "Rechnungs-ID",
        "issue_date": "Ausstellungsdatum",
        "due_date": "Zahlung fällig",
        "no_due_date": "Sofort fällig, rein netto.",
        "po_ref": "Ihre Auftragsnr.",
        "buyer_reference": "Referenz Auftraggeber",
        "seller_vat_id": "Umsatzsteuer-Identifikationsnummer",
        "buyer_vat_id": "USt-ID Leistungsempfänger",
        "pos": "Lfd.",
        "desc": "Beschreibung",
        "qty": "Umfang",
        "unit_price": "Stückpreis",
        "vat": "Steuersatz",
        "net": "Summe",
        "net_total": "Warenwert netto",
        "vat_line": "Umsatzsteuer {rate} % aus {base}",
        "gross_total": "Endbetrag",
        "amount_due": "Noch offen",
        "iban": "Überweisung an",
    },
    "en": {
        "title": "Billing Document",
        "number": "Document ref.",
        "issue_date": "Issued",
        "due_date": "Settle by",
        "no_due_date": "Due on receipt.",
        "po_ref": "Order ref.",
        "buyer_reference": "Client ref.",
        "seller_vat_id": "Tax registration",
        "buyer_vat_id": "Client tax registration",
        "pos": "Ln",
        "desc": "Details",
        "qty": "Volume",
        "unit_price": "Each",
        "vat": "Rate",
        "net": "Sum",
        "net_total": "Goods value",
        "vat_line": "Sales tax {rate} % on {base}",
        "gross_total": "Sum incl. tax",
        "amount_due": "Outstanding",
        "iban": "Remit to",
    },
}

# A credit note's own wording (v1.0 Stage 5): title, number and the invoice it
# corrects (BT-25). The other labels are the invoice's. Index 3 is the
# held-out wording, as above; none of it occurs in a dev layout.
CREDIT_LABELS = {
    "de": {
        "title": ("Gutschrift", "GUTSCHRIFT", "Gutschrift", "Gutschriftsanzeige"),
        "number": ("Gutschriftsnummer", "Gutschrift Nr.", "Beleg-Nr.", "Gutschrifts-ID"),
        "preceding_ref": (
            "zu Rechnung Nr.",
            "Bezug: Rechnung",
            "Ursprüngliche Rechnung",
            "Korrektur zu Beleg",
        ),
        "no_due_date": ("Der Betrag wird Ihrem Konto gutgeschrieben.",) * 4,
    },
    "en": {
        "title": ("Credit note", "CREDIT NOTE", "Credit Note", "Credit Memo"),
        "number": ("Credit note number", "Credit note no.", "Document no.", "Memo ref."),
        "preceding_ref": (
            "Original invoice",
            "Referenced invoice",
            "Original invoice",
            "Corrects invoice",
        ),
        "no_due_date": ("The amount will be credited to your account.",) * 4,
    },
}

UNITS = {
    "de": {"C62": "Stk", "HUR": "Std", "MTR": "m", "KMT": "km", "KGM": "kg"},
    "en": {"C62": "pcs", "HUR": "hrs", "MTR": "m", "KMT": "km", "KGM": "kg"},
}


@dataclass(frozen=True)
class Layout:
    name: str
    lang: str  # "de" | "en"
    wording: int  # index into the LABELS variants
    date_style: str  # "dots" 05.03.2026 | "iso" 2026-03-05 | "long" 5. März 2026 | "slashes"
    number_style: str  # "de" 1.234,56 | "en" 1,234.56 | "plain_de" 1234,56
    currency_style: str  # "symbol_after" | "code_after" | "symbol_before" | "none"
    seller_position: str  # "left" | "right" | "center"
    meta_position: str  # "right_box" | "left_list" | "inline"
    columns: tuple[str, ...]
    bank_position: str  # "footer" | "below_totals"
    iban_grouped: bool
    font: str  # "Helvetica" | "Times-Roman" | "Courier"
    label_separator: str = ":"

    def label(self, key: str) -> str:
        if self.wording == HELD_OUT_WORDING:
            return HELD_OUT_LABELS[self.lang][key]
        return LABELS[self.lang][key][self.wording]

    def credit_label(self, key: str) -> str:
        """A credit note's wording where it has its own, else the invoice's."""
        if key not in CREDIT_LABELS[self.lang]:
            return self.label(key)
        return CREDIT_LABELS[self.lang][key][self.wording]

    def unit(self, unit_code: str) -> str:
        return UNITS[self.lang][unit_code]

    def date(self, value: date) -> str:
        if self.date_style == "iso":
            return value.isoformat()
        if self.date_style == "dots":
            return f"{value:%d.%m.%Y}"
        if self.date_style == "slashes":
            return f"{value:%d/%m/%Y}"
        if self.date_style == "short":
            return f"{value:%d.%m.%y}"
        month = _MONTHS[self.lang][value.month - 1]
        return (
            f"{value.day}. {month} {value.year}"
            if self.lang == "de"
            else (f"{month} {value.day}, {value.year}")
        )

    def number(self, value: Decimal, places: int = 2) -> str:
        text = f"{value:,.{places}f}"  # 1,234.56
        if self.number_style == "en":
            return text
        if self.number_style == "ch":
            return text.replace(",", "'")
        swapped = text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
        if self.number_style == "plain_de":
            return swapped.replace(".", "")
        return swapped.replace(".", " ") if self.number_style == "space" else swapped

    def quantity(self, value: Decimal) -> str:
        places = 0 if value == value.to_integral_value() else 2
        return self.number(value, places)

    def money(self, value: Decimal, currency: str) -> str:
        amount = self.number(value)
        symbol = "€" if currency == "EUR" else currency
        return {
            "symbol_after": f"{amount} {symbol}",
            "code_after": f"{amount} {currency}",
            "symbol_before": f"{symbol} {amount}",
            "none": amount,
        }[self.currency_style]

    def iban(self, value: str) -> str:
        if not self.iban_grouped:
            return value
        return " ".join(value[i : i + 4] for i in range(0, len(value), 4))


_FULL = ("pos", "desc", "qty", "unit_price", "vat", "net")

LAYOUTS = (
    Layout(
        "de-classic",
        "de",
        0,
        "dots",
        "de",
        "symbol_after",
        "left",
        "right_box",
        _FULL,
        "footer",
        True,
        "Helvetica",
    ),
    Layout(
        "de-serif",
        "de",
        1,
        "long",
        "de",
        "code_after",
        "right",
        "left_list",
        _FULL,
        "below_totals",
        False,
        "Times-Roman",
    ),
    Layout(
        "de-typewriter",
        "de",
        2,
        "dots",
        "plain_de",
        "none",
        "center",
        "inline",
        _FULL,
        "footer",
        True,
        "Courier",
    ),
    Layout(
        "de-qty-first",
        "de",
        0,
        "iso",
        "de",
        "symbol_after",
        "right",
        "right_box",
        ("pos", "qty", "desc", "vat", "unit_price", "net"),
        "below_totals",
        True,
        "Helvetica",
    ),
    Layout(
        "de-compact",
        "de",
        1,
        "dots",
        "de",
        "symbol_before",
        "left",
        "inline",
        ("pos", "desc", "vat", "qty", "unit_price", "net"),
        "footer",
        False,
        "Helvetica",
        "",
    ),
    Layout(
        "de-formal",
        "de",
        2,
        "long",
        "de",
        "code_after",
        "center",
        "left_list",
        _FULL,
        "below_totals",
        True,
        "Times-Roman",
    ),
    Layout(
        "en-classic",
        "en",
        0,
        "iso",
        "en",
        "code_after",
        "left",
        "right_box",
        _FULL,
        "footer",
        True,
        "Helvetica",
    ),
    Layout(
        "en-us-dates",
        "en",
        1,
        "long",
        "en",
        "symbol_before",
        "right",
        "left_list",
        _FULL,
        "below_totals",
        False,
        "Times-Roman",
    ),
    Layout(
        "en-typewriter",
        "en",
        2,
        "slashes",
        "en",
        "none",
        "center",
        "inline",
        _FULL,
        "footer",
        True,
        "Courier",
    ),
    Layout(
        "en-qty-first",
        "en",
        0,
        "slashes",
        "en",
        "symbol_before",
        "right",
        "right_box",
        ("pos", "qty", "desc", "vat", "unit_price", "net"),
        "below_totals",
        True,
        "Helvetica",
    ),
    Layout(
        "en-german-numbers",
        "en",
        1,
        "dots",
        "de",
        "symbol_after",
        "left",
        "left_list",
        _FULL,
        "footer",
        False,
        "Helvetica",
    ),
    Layout(
        "de-english-numbers",
        "de",
        0,
        "iso",
        "en",
        "code_after",
        "left",
        "inline",
        ("pos", "desc", "qty", "vat", "unit_price", "net"),
        "below_totals",
        True,
        "Courier",
        "",
    ),
)

# Held-out layouts: the test split. Their wording, number and date formats and
# block positions appear in no dev layout. Extractor rules and prompts are
# developed against LAYOUTS only and must not be tuned on these.
#   date "short" = 05.03.26, number "ch" = 1'234.56, number "space" = 1 234,56,
#   seller "footer" = name as letterhead, address and VAT ID in the page footer,
#   meta "stacked" = label on one line, value on the next.
_QTY_FIRST = ("pos", "qty", "desc", "vat", "unit_price", "net")
_VAT_EARLY = ("pos", "desc", "vat", "qty", "unit_price", "net")
HELD_OUT_LAYOUTS = (
    Layout(
        "de-footer-seller",
        "de",
        3,
        "dots",
        "de",
        "symbol_after",
        "footer",
        "stacked",
        _FULL,
        "footer",
        True,
        "Helvetica",
    ),
    Layout(
        "de-short-dates",
        "de",
        3,
        "short",
        "space",
        "code_after",
        "left",
        "right_box",
        _QTY_FIRST,
        "below_totals",
        False,
        "Times-Roman",
    ),
    Layout(
        "en-swiss-numbers",
        "en",
        3,
        "long",
        "ch",
        "symbol_before",
        "footer",
        "left_list",
        _FULL,
        "footer",
        True,
        "Helvetica",
    ),
    Layout(
        "en-stacked",
        "en",
        3,
        "iso",
        "en",
        "none",
        "right",
        "stacked",
        _VAT_EARLY,
        "below_totals",
        False,
        "Courier",
    ),
)
ALL_LAYOUTS = LAYOUTS + HELD_OUT_LAYOUTS
