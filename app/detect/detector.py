"""Decide what kind of document a blob of bytes is.

Detection never raises on bad input: anything it cannot classify is UNKNOWN,
so a hostile or broken file ends up in review instead of crashing ingest.
"""

import io
import logging

from lxml import etree
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app import safe_xml
from app.models.formats import DocumentFormat

logger = logging.getLogger(__name__)

UBL_ROOTS = {
    "{urn:oasis:names:specification:ubl:schema:xsd:Invoice-2}Invoice",
    "{urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2}CreditNote",
}
CII_ROOT = "{urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100}CrossIndustryInvoice"

# Attachment names defined by ZUGFeRD 1.0 / 2.x, Factur-X and XRechnung-in-PDF.
EMBEDDED_INVOICE_NAMES = {
    "factur-x.xml",
    "zugferd-invoice.xml",
    "xrechnung.xml",
}

IMAGE_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",  # JPEG
    b"II*\x00",  # TIFF little endian
    b"MM\x00*",  # TIFF big endian
)

# A scanned page yields no text or only stray characters.
MIN_TEXT_CHARS = 20


def detect(data: bytes) -> DocumentFormat:
    if data.startswith(b"%PDF-"):
        return _detect_pdf(data)
    if data.startswith(IMAGE_SIGNATURES):
        return DocumentFormat.IMAGE
    return _detect_xml(data)


def _detect_xml(data: bytes) -> DocumentFormat:
    try:
        root = safe_xml.parse(data)
    except (etree.XMLSyntaxError, ValueError) as exc:
        logger.info("not classifiable as XML invoice: %s", exc)
        return DocumentFormat.UNKNOWN
    if root.tag in UBL_ROOTS:
        return DocumentFormat.UBL
    if root.tag == CII_ROOT:
        return DocumentFormat.CII
    return DocumentFormat.UNKNOWN


def extract_embedded_invoice_xml(data: bytes) -> bytes | None:
    """Return the embedded invoice XML of a ZUGFeRD/Factur-X PDF, if any.

    Only attachments with a known invoice file name that also parse as CII or
    UBL count; an arbitrary XML attachment does not make a PDF an e-invoice.
    """
    try:
        reader = PdfReader(io.BytesIO(data))
        attachments = reader.attachments
        for name in attachments:
            if name.lower() not in EMBEDDED_INVOICE_NAMES:
                continue
            for content in attachments[name]:
                if _detect_xml(content) in {DocumentFormat.CII, DocumentFormat.UBL}:
                    return content
    except (PyPdfError, ValueError, KeyError, TypeError, OSError) as exc:
        logger.info("could not read PDF attachments: %s", exc)
    return None


def _detect_pdf(data: bytes) -> DocumentFormat:
    if extract_embedded_invoice_xml(data) is not None:
        return DocumentFormat.ZUGFERD_PDF
    try:
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) == 0:
            return DocumentFormat.UNKNOWN
        text = "".join(page.extract_text() or "" for page in reader.pages)
    except (PyPdfError, ValueError, KeyError, TypeError, OSError) as exc:
        logger.info("unreadable PDF: %s", exc)
        return DocumentFormat.UNKNOWN
    if len(text.strip()) >= MIN_TEXT_CHARS:
        return DocumentFormat.PDF_TEXT
    return DocumentFormat.IMAGE
