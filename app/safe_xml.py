"""XML parsing for untrusted input.

Invoices arrive from outside, so every XML parse in this project goes through
here: no entity expansion, no DTD loading, no network access.
"""

from lxml import etree


def make_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        load_dtd=False,
        no_network=True,
        huge_tree=False,
        remove_comments=True,
        remove_pis=True,
    )


def parse(data: bytes) -> etree._Element:
    """Parse untrusted XML and return the root element.

    Raises etree.XMLSyntaxError on malformed input, and ValueError if the
    document declares entities (never legitimate in an invoice, and the
    vector for entity-expansion attacks).
    """
    root = etree.fromstring(data, parser=make_parser())  # noqa: S320 - hardened parser
    if root.getroottree().docinfo.internalDTD is not None:
        raise ValueError("XML with a DOCTYPE declaration is not accepted")
    return root
