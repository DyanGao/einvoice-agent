"""Rule-based extractor: keyword lists and regular expressions over PDF text.

This is the baseline the LLM pipeline has to beat (EVAL_PLAN §3, system 1),
not a production path. It only knows what is written here: common German and
English labels, a few date and number formats, and one heuristic per field.
"""

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation

from app.detect import detect
from app.extract.draft import InvoiceDraft, LineDraft, PartyDraft, VatBreakdownDraft
from app.extract.ocr import OcrError, ocr_text
from app.extract.pdf_text import layout_text
from app.models import DocumentFormat

_AMOUNT = r"-?\d{1,3}(?:[.,]\d{3})*[.,]\d{2}|-?\d+[.,]\d{2}"
_AMOUNT_RE = re.compile(_AMOUNT)
_COLUMN_GAP = re.compile(r"\s{2,}")
_VAT_ID_RE = re.compile(r"\bDE\s?\d{9}\b")
_IBAN_RE = re.compile(r"\bDE\d{2}(?:\s?\d{4}){4}\s?\d{2}\b")
_PERCENT_RE = re.compile(r"(\d{1,2}(?:[.,]\d+)?)\s*%")

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("januar", "january"),
            ("februar", "february"),
            ("märz", "march"),
            ("april",),
            ("mai", "may"),
            ("juni", "june"),
            ("juli", "july"),
            ("august",),
            ("september",),
            ("oktober", "october"),
            ("november",),
            ("dezember", "december"),
        ),
        start=1,
    )
    for name in names
}
_DATE_PATTERNS = (
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})\.?\s+([A-Za-zä]+)\s+(\d{4})\b"), "d_month_y"),
    (re.compile(r"\b([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\b"), "month_d_y"),
)

_LABELS = {
    "number": (
        # A credit note's own number first: it may also name the invoice it
        # corrects with "Rechnung Nr." (preceding_ref below).
        "Gutschriftsnummer",
        "Gutschrift Nr.",
        "Gutschrift-Nr.",
        "Credit note number",
        "Credit note no.",
        "Rechnungsnummer",
        "Rechnungs-Nr.",
        "Rechnung Nr.",
        "Beleg-Nr.",
        "Document no.",
        "Invoice number",
        "Invoice no.",
        "Invoice #",
    ),
    "issue_date": (
        "Rechnungsdatum",
        "Belegdatum",
        "Invoice date",
        "Date of issue",
        "Datum",
        "Date",
    ),
    "due_date": ("Zahlbar bis", "Fällig am", "Zahlungsziel", "Due date", "Payment due", "Pay by"),
    "po_ref": (
        "Bestellnummer",
        "Bestell-Nr.",
        "Ihre Bestellung",
        "PO number",
        "Purchase order",
        "Your order",
    ),
    # BT-25: the invoice a credit note corrects.
    "preceding_ref": (
        "zu Rechnung Nr.",
        "zur Rechnung Nr.",
        "Bezug: Rechnung",
        "Ursprüngliche Rechnung",
        "Original invoice",
        "Referenced invoice",
    ),
    "buyer_reference": (
        "Leitweg-ID",
        "Ihr Zeichen",
        "Käuferreferenz",
        "Buyer reference",
        "Your reference",
        "Routing ID",
    ),
    "net_total": (
        "Nettobetrag",
        "Zwischensumme",
        "Summe netto",
        "Net amount",
        "Subtotal",
        "Total excl. VAT",
    ),
    "gross_total": (
        "Gesamtbetrag",
        "Rechnungsbetrag",
        "Bruttobetrag",
        "Total incl. VAT",
        "Invoice total",
        "Total",
    ),
    "amount_due": (
        "Zu zahlen",
        "Zahlbetrag",
        "Offener Betrag",
        "Amount due",
        "Balance due",
        "To pay",
    ),
}
_TAX_WORDS = ("USt", "MwSt", "VAT", "Tax")
_TITLE_WORDS = ("rechnung", "invoice")
# A credit note's title. Not "Rechnungskorrektur" (384, a corrected invoice).
_CREDIT_WORDS = ("gutschrift", "credit note")


def parse_amount(text: str) -> Decimal | None:
    """1.234,56 / 1,234.56 / 1234,56 → Decimal. The last separator is the decimal one."""
    text = text.strip()
    if not text:
        return None
    decimal_at = max(text.rfind(","), text.rfind("."))
    if decimal_at == -1:
        digits = text
    else:
        whole = text[:decimal_at].replace(".", "").replace(",", "")
        digits = f"{whole}.{text[decimal_at + 1 :]}"
    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


def parse_date(text: str) -> date | None:
    for pattern, order in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        a, b, c = match.groups()
        try:
            if order == "ymd":
                return date(int(a), int(b), int(c))
            if order == "dmy":
                return date(int(c), int(b), int(a))
            if order == "d_month_y" and b.lower() in _MONTHS:
                return date(int(c), _MONTHS[b.lower()], int(a))
            if order == "month_d_y" and a.lower() in _MONTHS:
                return date(int(c), _MONTHS[a.lower()], int(b))
        except ValueError:
            continue
    return None


def _after_label(lines: list[str], labels: tuple[str, ...]) -> str | None:
    """Text following the first label found, up to the next column gap."""
    for label in labels:
        pattern = re.compile(rf"(?<![\w-]){re.escape(label)}(?![\w-])\s*:?\s*(.*)", re.IGNORECASE)
        for line in lines:
            match = pattern.search(line)
            if match and match.group(1).strip():
                return match.group(1).strip()
    return None


def _first_cell(text: str | None) -> str | None:
    if text is None:
        return None
    return _COLUMN_GAP.split(text)[0].strip() or None


def _amount_after(lines: list[str], labels: tuple[str, ...]) -> Decimal | None:
    rest = _after_label(lines, labels)
    if rest is None:
        return None
    amounts = _AMOUNT_RE.findall(rest)
    return parse_amount(amounts[-1]) if amounts else None


def _valid_iban(iban: str) -> bool:
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 == 1


def _vat_breakdown(lines: list[str]) -> tuple[VatBreakdownDraft, ...]:
    groups = []
    for line in lines:
        if not any(word.lower() in line.lower() for word in _TAX_WORDS):
            continue
        percent = _PERCENT_RE.search(line)
        amounts = _AMOUNT_RE.findall(_PERCENT_RE.sub(" ", line))
        if percent and len(amounts) == 2:
            groups.append(
                VatBreakdownDraft(
                    category="S",
                    rate=parse_amount(percent.group(1)),
                    taxable_amount=parse_amount(amounts[0]),
                    tax_amount=parse_amount(amounts[1]),
                )
            )
    return tuple(groups)


def _line_item(line: str) -> LineDraft | None:
    cells = [cell for cell in _COLUMN_GAP.split(line.strip()) if cell]
    if len(cells) < 5 or not cells[0].isdigit():
        return None
    rate = quantity = description = None
    amounts = []
    for cell in cells[1:]:
        percent = _PERCENT_RE.fullmatch(cell)
        quantity_match = re.fullmatch(rf"({_AMOUNT}|\d+)\s+[A-Za-z]+", cell)
        if percent:
            rate = parse_amount(percent.group(1))
        elif quantity_match and quantity is None:
            quantity = parse_amount(quantity_match.group(1))
        elif _AMOUNT_RE.fullmatch(cell):
            amounts.append(parse_amount(cell))
        elif description is None:
            description = cell
    if rate is None or len(amounts) != 2:
        return None
    return LineDraft(
        line_id=cells[0],
        description=description,
        quantity=quantity,
        unit_price=amounts[0],
        net=amounts[1],
        vat_rate=rate,
    )


def _buyer_name(lines: list[str], seller_vat_line: int | None) -> str | None:
    """The first text line below the seller block is taken as the addressee."""
    if seller_vat_line is None:
        return None
    for line in lines[seller_vat_line + 1 :]:
        if line.strip():
            return _first_cell(line.strip())
    return None


def _without_preceding(line: str, preceding_ref: str) -> str:
    """The line without "zu Rechnung Nr. <preceding_ref>": the credit note's own
    number may stand on the same line."""
    for label in _LABELS["preceding_ref"]:
        pattern = rf"(?<![\w-]){re.escape(label)}(?![\w-])\s*:?\s*{re.escape(preceding_ref)}"
        line = re.sub(pattern, "   ", line, flags=re.IGNORECASE)
    return line


def _number(lines: list[str], preceding_ref: str | None) -> str | None:
    """The document's own number; not the invoice a credit note names."""
    if preceding_ref is not None:
        lines = [_without_preceding(line, preceding_ref) for line in lines]
        # Named again elsewhere ("Betreff: Rechnung Nr. …"): not this document's.
        lines = [line for line in lines if preceding_ref not in line]
    return _first_cell(_after_label(lines, _LABELS["number"]))


def extract_from_text(text: str) -> InvoiceDraft:
    lines = [line.rstrip() for line in text.splitlines()]
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return InvoiceDraft(source_format=DocumentFormat.PDF_TEXT)

    vat_ids = [re.sub(r"\s", "", match) for match in _VAT_ID_RE.findall(text)]
    seller_vat_line = next(
        (index for index, line in enumerate(lines) if _VAT_ID_RE.search(line)), None
    )
    ibans = [re.sub(r"\s", "", match) for match in _IBAN_RE.findall(text)]
    breakdown = _vat_breakdown(lines)
    net_total = _amount_after(lines, _LABELS["net_total"])
    issue_text = _after_label(lines, _LABELS["issue_date"])
    due_text = _after_label(lines, _LABELS["due_date"])
    has_title = any(word in text.lower() for word in _TITLE_WORDS)
    credit = any(word in text.lower() for word in _CREDIT_WORDS)
    preceding_ref = _first_cell(_after_label(lines, _LABELS["preceding_ref"]))
    tax_amounts = [group.tax_amount for group in breakdown if group.tax_amount is not None]

    return InvoiceDraft(
        number=_number(lines, preceding_ref),
        issue_date=parse_date(issue_text) if issue_text else None,
        type_code="381" if credit else "380" if has_title else None,
        currency="EUR",  # rule: German accounts payable, euro unless stated otherwise
        due_date=parse_date(due_text) if due_text else None,
        buyer_reference=_first_cell(_after_label(lines, _LABELS["buyer_reference"])),
        po_ref=_first_cell(_after_label(lines, _LABELS["po_ref"])),
        preceding_ref=preceding_ref,
        seller=PartyDraft(name=non_empty[0].strip(), vat_id=vat_ids[0] if vat_ids else None),
        buyer=PartyDraft(
            name=_buyer_name(lines, seller_vat_line),
            vat_id=vat_ids[1] if len(vat_ids) > 1 else None,
        ),
        payment_ibans=tuple(dict.fromkeys(iban for iban in ibans if _valid_iban(iban))),
        lines=tuple(item for item in map(_line_item, lines) if item is not None),
        vat_breakdown=breakdown,
        line_net_total=net_total,
        net_total=net_total,
        vat_total=sum(tax_amounts, Decimal(0)) if tax_amounts else None,
        gross_total=_amount_after(lines, _LABELS["gross_total"]),
        amount_due=_amount_after(lines, _LABELS["amount_due"]),
        source_format=DocumentFormat.PDF_TEXT,
    )


class BaselineExtractor:
    """Text PDFs are read directly; images and scanned PDFs go through OCR first."""

    def __init__(self, ocr: Callable[[bytes], str] = ocr_text) -> None:
        self._ocr = ocr

    def extract(self, data: bytes) -> InvoiceDraft:
        fmt = detect(data)
        if fmt is DocumentFormat.IMAGE:
            try:
                text = self._ocr(data)
            except OcrError:
                text = ""
        else:
            text = layout_text(data)
        return extract_from_text(text).model_copy(update={"source_format": fmt})
