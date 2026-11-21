"""Render a canonical invoice as a text PDF in one of the layouts, and write
the synthetic PDF set with gold records.

    python -m eval.generators.pdf --count 150 --out eval/out/pdf
"""

import argparse
import io
import json
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from app.models import DocumentFormat, Invoice
from eval.generators.canonical import CanonicalInvoice, Company, build_invoice
from eval.generators.pdf_layouts import ALL_LAYOUTS, HELD_OUT_LAYOUTS, Layout
from eval.generators.structured import DEFAULT_SEED, GOLD_FILE

PAGE_WIDTH, PAGE_HEIGHT = A4
LEFT, RIGHT = 56, PAGE_WIDTH - 56
BOLD = {"Helvetica": "Helvetica-Bold", "Times-Roman": "Times-Bold", "Courier": "Courier-Bold"}
COLUMN_WIDTHS = {"pos": 34, "desc": 190, "qty": 62, "unit_price": 76, "vat": 44, "net": 77}
RIGHT_ALIGNED = {"qty", "unit_price", "vat", "net"}
_HELD_OUT_NAMES = {layout.name for layout in HELD_OUT_LAYOUTS}


def split_of(layout: Layout) -> str:
    """Test = layouts no rule or prompt was developed against. Splitting by
    invoice instead would put every layout on both sides and measure nothing."""
    return "test" if layout.name in _HELD_OUT_NAMES else "dev"


class _Page:
    def __init__(self, layout: Layout, *, negative: bool = False) -> None:
        self.buffer = io.BytesIO()
        self.canvas = Canvas(self.buffer, pagesize=A4, invariant=1)
        self.layout = layout
        # A credit note printed with its amounts negative, as many are.
        self.negative = negative

    def amount(self, value: Decimal) -> Decimal:
        return -value if self.negative else value

    def text(self, x: float, y: float, value: str, *, size: int = 10, bold: bool = False) -> None:
        self.canvas.setFont(BOLD[self.layout.font] if bold else self.layout.font, size)
        self.canvas.drawString(x, y, value)

    def text_right(self, x: float, y: float, value: str, *, bold: bool = False) -> None:
        self.canvas.setFont(BOLD[self.layout.font] if bold else self.layout.font, 10)
        self.canvas.drawRightString(x, y, value)

    def text_center(self, y: float, value: str, *, size: int = 10, bold: bool = False) -> None:
        self.canvas.setFont(BOLD[self.layout.font] if bold else self.layout.font, size)
        self.canvas.drawCentredString(PAGE_WIDTH / 2, y, value)

    def rule(self, y: float) -> None:
        self.canvas.setLineWidth(0.5)
        self.canvas.line(LEFT, y, RIGHT, y)

    def finish(self) -> bytes:
        self.canvas.showPage()
        self.canvas.save()
        return self.buffer.getvalue()


def _labelled(layout: Layout, key: str, value: str) -> str:
    return f"{layout.label(key)}{layout.label_separator} {value}"


def _seller_footer_line(layout: Layout, seller: Company) -> str:
    parts = (
        seller.name,
        seller.street,
        f"{seller.postcode} {seller.city}",
        _labelled(layout, "seller_vat_id", seller.vat_id),
    )
    return " · ".join(parts)


def _seller_block(page: _Page, seller: Company) -> None:
    layout = page.layout
    if layout.seller_position == "footer":
        page.text(LEFT, PAGE_HEIGHT - 60, seller.name, size=18, bold=True)
        return
    rows = [
        (seller.name, 14, True),
        (seller.street, 10, False),
        (f"{seller.postcode} {seller.city}", 10, False),
        (seller.email, 10, False),
        (_labelled(layout, "seller_vat_id", seller.vat_id), 10, False),
    ]
    y = PAGE_HEIGHT - 60
    for value, size, bold in rows:
        if layout.seller_position == "center":
            page.text_center(y, value, size=size, bold=bold)
        elif layout.seller_position == "right":
            page.canvas.setFont(BOLD[layout.font] if bold else layout.font, size)
            page.canvas.drawRightString(RIGHT, y, value)
        else:
            page.text(LEFT, y, value, size=size, bold=bold)
        y -= size + 4


def _buyer_block(page: _Page, buyer: Company) -> None:
    y = PAGE_HEIGHT - 170
    for value in (buyer.name, buyer.street, f"{buyer.postcode} {buyer.city}"):
        page.text(LEFT, y, value)
        y -= 14
    if buyer.vat_id:
        page.text(LEFT, y, _labelled(page.layout, "buyer_vat_id", buyer.vat_id))


def _is_credit(invoice: CanonicalInvoice) -> bool:
    return invoice.type_code == "381"


def _label(layout: Layout, invoice: CanonicalInvoice, key: str) -> str:
    return layout.credit_label(key) if _is_credit(invoice) else layout.label(key)


def _meta_rows(layout: Layout, invoice: CanonicalInvoice) -> list[tuple[str, str]]:
    rows = [(_label(layout, invoice, "number"), invoice.number)]
    if invoice.preceding_ref:
        rows.append((layout.credit_label("preceding_ref"), invoice.preceding_ref))
    rows.append((layout.label("issue_date"), layout.date(invoice.issue_date)))
    if invoice.due_date:
        rows.append((layout.label("due_date"), layout.date(invoice.due_date)))
    if invoice.po_ref:
        rows.append((layout.label("po_ref"), invoice.po_ref))
    if invoice.buyer_reference:
        rows.append((layout.label("buyer_reference"), invoice.buyer_reference))
    return rows


def _meta_block(page: _Page, invoice: CanonicalInvoice) -> None:
    layout = page.layout
    rows = _meta_rows(layout, invoice)
    title_y = PAGE_HEIGHT - 260
    page.text(LEFT, title_y, _label(layout, invoice, "title"), size=16, bold=True)
    separator = layout.label_separator
    if layout.meta_position == "right_box":
        y = PAGE_HEIGHT - 170
        for label, value in rows:
            page.text(340, y, f"{label}{separator}")
            page.text_right(RIGHT, y, value)
            y -= 14
    elif layout.meta_position == "left_list":
        y = title_y - 20
        for label, value in rows:
            page.text(LEFT, y, f"{label}{separator}")
            page.text(LEFT + 130, y, value)
            y -= 13
    elif layout.meta_position == "stacked":  # label on one line, value below it
        y = title_y - 22
        for start in range(0, len(rows), 3):
            x = LEFT
            for label, value in rows[start : start + 3]:
                page.text(x, y, label, size=8)
                page.text(x, y - 11, value)
                x += 165
            y -= 30
    else:  # inline: label/value pairs, three per row
        y = title_y - 20
        for start in range(0, len(rows), 3):
            x = LEFT
            for label, value in rows[start : start + 3]:
                text = f"{label}{separator} {value}"
                page.text(x, y, text, size=9)
                # A credit note's next pair after this one, never over it (its
                # labels are longer). An invoice keeps its places, overlaps and
                # all: the recorded model answers are for those exact files.
                width = page.canvas.stringWidth(text, page.layout.font, 9)
                x = max(x + 165, x + width + 18) if _is_credit(invoice) else x + 165
            y -= 13


def _table(page: _Page, invoice: CanonicalInvoice) -> float:
    layout = page.layout
    edges, x = {}, LEFT
    for column in layout.columns:
        edges[column] = (x, x + COLUMN_WIDTHS[column])
        x += COLUMN_WIDTHS[column]

    def cell(column: str, y: float, value: str, *, bold: bool = False) -> None:
        left, right = edges[column]
        if column in RIGHT_ALIGNED:
            page.text_right(right - 4, y, value, bold=bold)
        else:
            page.text(left, y, value, bold=bold)

    y = PAGE_HEIGHT - 345
    for column in layout.columns:
        cell(column, y, layout.label(column), bold=True)
    page.rule(y - 5)
    y -= 20
    for item in invoice.items:
        values = {
            "pos": item.line_id,
            "desc": item.name,
            "qty": f"{layout.quantity(item.quantity)} {layout.unit(item.unit_code)}",
            "unit_price": layout.number(item.unit_price),
            "vat": f"{item.vat_rate} %",
            "net": layout.number(page.amount(item.net)),
        }
        for column in layout.columns:
            cell(column, y, values[column])
        y -= 17
    page.rule(y + 8)
    return y


def _totals(page: _Page, invoice: CanonicalInvoice, y: float) -> float:
    layout = page.layout
    rows = [(layout.label("net_total"), invoice.net_total, False)]
    for group in invoice.tax_groups:
        label = layout.label("vat_line").format(
            rate=group.rate, base=layout.number(group.taxable_amount)
        )
        rows.append((label, group.tax_amount, False))
    rows.append((layout.label("gross_total"), invoice.gross_total, True))
    rows.append((layout.label("amount_due"), invoice.amount_due, True))
    y -= 12
    for label, amount, bold in rows:
        page.text_right(RIGHT - 110, y, label, bold=bold)
        page.text_right(
            RIGHT - 4, y, layout.money(page.amount(amount), invoice.currency), bold=bold
        )
        y -= 15
    return y


def _payment(page: _Page, invoice: CanonicalInvoice, y: float) -> None:
    layout = page.layout
    lines = [] if invoice.due_date else [_label(layout, invoice, "no_due_date")]
    lines += [_labelled(layout, "iban", layout.iban(iban)) for iban in invoice.ibans]
    footer = []
    if layout.bank_position == "footer":
        footer += lines
    else:
        y -= 20
        for line in lines:
            page.text(LEFT, y, line, size=9)
            y -= 13
    if layout.seller_position == "footer":
        footer.append(_seller_footer_line(layout, invoice.seller))
    if footer:
        y = 40 + 12 * len(footer)
        page.rule(y + 11)
        for line in footer:
            page.text(LEFT, y, line, size=7)
            y -= 12


@dataclass(frozen=True)
class Note:
    """Free text printed on the invoice outside every field: what an
    injection probe (eval/generators/probes.py) adds to a document.
    `placement` "note" prints it in small type above the footer; "hidden"
    prints it there in white, invisible on the page but in its text."""

    text: str
    placement: str
    WIDTH = 105  # characters per printed line

    def lines(self) -> list[str]:
        lines, current = [], ""
        for word in self.text.split():
            if current and len(current) + 1 + len(word) > self.WIDTH:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        return [*lines, current] if current else lines


def _notes(page: _Page, notes: tuple[Note, ...]) -> None:
    y = 132
    for note in notes:
        if note.placement not in ("note", "hidden"):
            raise ValueError(f"unknown placement {note.placement!r}")
        page.canvas.setFillColorRGB(*((1, 1, 1) if note.placement == "hidden" else (0, 0, 0)))
        for line in note.lines():
            page.text(LEFT, y, line, size=7)
            y -= 9
    page.canvas.setFillColorRGB(0, 0, 0)


def render_pdf(
    invoice: CanonicalInvoice,
    layout: Layout,
    notes: tuple[Note, ...] = (),
    *,
    negative: bool = False,
) -> bytes:
    """`negative` prints a credit note's line and total amounts with a minus."""
    page = _Page(layout, negative=negative)
    _seller_block(page, invoice.seller)
    _buyer_block(page, invoice.buyer)
    _meta_block(page, invoice)
    y = _table(page, invoice)
    y = _totals(page, invoice, y)
    _payment(page, invoice, y)
    if notes:  # drawn only when asked: every other PDF keeps its exact bytes
        _notes(page, notes)
    return page.finish()


@dataclass(frozen=True)
class GeneratedPdf:
    doc_id: str
    filename: str
    content: bytes
    layout: str
    split: str
    invoice: Invoice

    def gold_record(self) -> dict:
        return {
            "id": self.doc_id,
            "file": self.filename,
            "format": DocumentFormat.PDF_TEXT.value,
            "layout": self.layout,
            "split": self.split,
            # Not applicable to unstructured documents: there is no validator verdict.
            "expected_valid": None,
            "expected_rule": None,
            "invoice": self.invoice.model_dump(mode="json"),
        }


def generate(count: int, seed: int = DEFAULT_SEED) -> Iterator[GeneratedPdf]:
    for index in range(count):
        canonical = build_invoice(seed, index)
        layout = ALL_LAYOUTS[index % len(ALL_LAYOUTS)]
        doc_id = f"pdf-{index:04d}"
        yield GeneratedPdf(
            doc_id=doc_id,
            filename=f"{doc_id}.pdf",
            content=render_pdf(canonical, layout),
            layout=layout.name,
            split=split_of(layout),
            invoice=canonical.to_gold(DocumentFormat.PDF_TEXT),
        )


def write_set(out_dir: Path, count: int, seed: int = DEFAULT_SEED) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    with (out_dir / GOLD_FILE).open("w", encoding="utf-8") as gold:
        for document in generate(count, seed):
            (out_dir / document.filename).write_bytes(document.content)
            gold.write(json.dumps(document.gold_record(), ensure_ascii=False, sort_keys=True))
            gold.write("\n")
            written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=150)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path("eval/out/pdf"))
    args = parser.parse_args()
    print(f"wrote {write_set(args.out, args.count, args.seed)} documents to {args.out}")


if __name__ == "__main__":
    main()
