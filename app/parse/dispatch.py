from lxml import etree
from pydantic import ValidationError

from app import safe_xml
from app.detect import detect, extract_embedded_invoice_xml
from app.models import DocumentFormat, Invoice
from app.parse.cii import parse_cii
from app.parse.fields import InvoiceParseError
from app.parse.ubl import parse_ubl

_XML_PARSERS = {DocumentFormat.UBL: parse_ubl, DocumentFormat.CII: parse_cii}


def parse_invoice(data: bytes) -> Invoice:
    """Parse a structured e-invoice (UBL, CII or ZUGFeRD/Factur-X PDF).

    Raises InvoiceParseError for anything else, including PDFs and scans:
    those belong to the extraction path, which is not this function's job.
    """
    fmt = detect(data)
    if fmt is DocumentFormat.ZUGFERD_PDF:
        xml = extract_embedded_invoice_xml(data)
        if xml is None:  # detect() just found it; guards against the two drifting apart
            raise InvoiceParseError("ZUGFeRD PDF without readable embedded invoice XML")
        return _parse_xml(xml, detect(xml)).model_copy(
            update={"source_format": DocumentFormat.ZUGFERD_PDF}
        )
    if fmt not in _XML_PARSERS:
        raise InvoiceParseError(f"not a structured e-invoice (detected: {fmt.value})")
    return _parse_xml(data, fmt)


def _parse_xml(xml: bytes, fmt: DocumentFormat) -> Invoice:
    try:
        return _XML_PARSERS[fmt](safe_xml.parse(xml))
    except (etree.XMLSyntaxError, ValidationError) as exc:
        raise InvoiceParseError(f"invalid {fmt.value} invoice: {exc}") from exc
