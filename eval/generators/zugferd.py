"""Wrap a CII invoice in a PDF carrier, as ZUGFeRD / Factur-X does.

Limitation: the carrier is an ordinary PDF with the XML attached under the
Factur-X file name. It is NOT a conformant PDF/A-3 file and carries no
Factur-X XMP metadata, so it exercises attachment detection, extraction and
XML validation — not PDF/A conformance checking.
"""

import io

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from eval.generators.canonical import CanonicalInvoice

ATTACHMENT_NAME = "factur-x.xml"


def _pdf_text(value: str) -> str:
    latin = value.encode("latin-1", errors="replace").decode("latin-1")
    return latin.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def render_zugferd_pdf(invoice: CanonicalInvoice, cii_xml: bytes) -> bytes:
    rows = [
        f"Rechnung {invoice.number}",
        f"{invoice.seller.name} an {invoice.buyer.name}",
        f"Rechnungsdatum {invoice.issue_date:%d.%m.%Y}",
        *(f"{item.quantity} x {item.name}  {item.net:.2f}" for item in invoice.items),
        f"Gesamtbetrag {invoice.gross_total:.2f} {invoice.currency}",
    ]
    content = (
        "BT /F1 11 Tf 56 780 Td 16 TL "
        + " ".join(f"({_pdf_text(row)}) Tj T*" for row in rows)
        + " ET"
    )

    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(content.encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_attachment(ATTACHMENT_NAME, cii_xml)

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
