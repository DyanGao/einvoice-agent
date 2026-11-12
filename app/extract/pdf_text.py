import io

from pypdf import PdfReader
from pypdf.errors import PyPdfError


def layout_text(data: bytes) -> str:
    """Text of a PDF with horizontal positions kept, so that the cells of a
    table row stay on one line. Empty string if the PDF cannot be read."""
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text(extraction_mode="layout") or "" for page in reader.pages)
    except (PyPdfError, ValueError, KeyError, TypeError, OSError):
        return ""
