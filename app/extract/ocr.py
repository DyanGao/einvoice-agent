"""Text recognition for scans, with Tesseract.

The language models come from a project-local folder (`make ocr-data`) so the
result does not depend on what happens to be installed system-wide.
"""

import io
from pathlib import Path

import pypdfium2 as pdfium
import pytesseract
from PIL import Image, UnidentifiedImageError

LANGUAGES = ("deu", "eng")
DEFAULT_TESSDATA_DIR = Path(".tessdata")
SCANNED_PDF_DPI = 200
# psm 6: treat the page as one block of text and keep reading order row by row.
# preserve_interword_spaces keeps the gaps between table columns.
_CONFIG = "--psm 6 -c preserve_interword_spaces=1"


class OcrUnavailable(RuntimeError):
    """Tesseract or its language data is missing on this machine."""


class OcrError(RuntimeError):
    """The document could not be turned into an image to recognise."""


def _pages(data: bytes) -> list[Image.Image]:
    if data.startswith(b"%PDF-"):
        try:
            document = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            raise OcrError(f"unreadable PDF: {exc}") from exc
        try:
            return [page.render(scale=SCANNED_PDF_DPI / 72).to_pil() for page in document]
        finally:
            document.close()
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise OcrError(f"unreadable image: {exc}") from exc
    return [image]


def ocr_text(data: bytes, tessdata_dir: Path = DEFAULT_TESSDATA_DIR) -> str:
    """Recognised text of an image or scanned PDF, pages joined by blank lines."""
    missing = [lang for lang in LANGUAGES if not (tessdata_dir / f"{lang}.traineddata").exists()]
    if missing:
        raise OcrUnavailable(
            f"Tesseract language data missing in {tessdata_dir}: {', '.join(missing)}. "
            "Run `make ocr-data`."
        )
    config = f'--tessdata-dir "{tessdata_dir.resolve()}" {_CONFIG}'
    try:
        texts = [
            pytesseract.image_to_string(page, lang="+".join(LANGUAGES), config=config)
            for page in _pages(data)
        ]
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrUnavailable("the tesseract program is not installed") from exc
    return "\n\n".join(texts)
