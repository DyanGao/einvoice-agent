"""Build tiny PDFs in memory so detector tests need no binary fixtures."""

import io

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def _writer_with_page(text: str | None) -> PdfWriter:
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    if text is not None:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    return writer


def make_pdf(text: str | None = None, attachments: dict[str, bytes] | None = None) -> bytes:
    writer = _writer_with_page(text)
    for name, content in (attachments or {}).items():
        writer.add_attachment(name, content)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
