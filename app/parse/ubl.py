"""UBL 2.1 Invoice / CreditNote → Invoice (EN 16931 subset, see app.models.invoice)."""

from lxml import etree

from app.models import DocumentFormat, Invoice, Line, Party, VatBreakdown
from app.parse.fields import InvoiceParseError, Reader, compact_id, compact_ids, to_date

NS = {
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
R = Reader(NS)
ISO_DATE = "%Y-%m-%d"

# Element names that differ between the two UBL document types.
VARIANTS = {
    "Invoice": {
        "type_code": "cbc:InvoiceTypeCode",
        "due_date": "cbc:DueDate",
        "line": "cac:InvoiceLine",
        "quantity": "cbc:InvoicedQuantity",
    },
    "CreditNote": {
        "type_code": "cbc:CreditNoteTypeCode",
        "due_date": "cac:PaymentMeans/cbc:PaymentDueDate",
        "line": "cac:CreditNoteLine",
        "quantity": "cbc:CreditedQuantity",
    },
}


def parse_ubl(root: etree._Element) -> Invoice:
    kind = etree.QName(root).localname
    if kind not in VARIANTS:
        raise InvoiceParseError(f"not a UBL invoice or credit note: {root.tag}")
    names = VARIANTS[kind]
    currency = R.required(root, "cbc:DocumentCurrencyCode", "BT-5 currency")
    totals = R.find(root, "cac:LegalMonetaryTotal")
    if totals is None:
        raise InvoiceParseError("missing BG-22 document totals (cac:LegalMonetaryTotal)")
    due_date = R.text(root, names["due_date"])

    return Invoice(
        number=R.required(root, "cbc:ID", "BT-1 invoice number"),
        issue_date=to_date(
            R.required(root, "cbc:IssueDate", "BT-2 issue date"), ISO_DATE, "BT-2 issue date"
        ),
        type_code=R.required(root, names["type_code"], "BT-3 type code"),
        currency=currency,
        due_date=None if due_date is None else to_date(due_date, ISO_DATE, "BT-9 due date"),
        buyer_reference=R.text(root, "cbc:BuyerReference"),
        po_ref=_po_ref(root),
        preceding_ref=R.text(root, "cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID"),
        seller=_party(root, "cac:AccountingSupplierParty/cac:Party", "BT-27 seller name"),
        buyer=_party(root, "cac:AccountingCustomerParty/cac:Party", "BT-44 buyer name"),
        payment_ibans=compact_ids(
            node.text
            for node in R.findall(root, "cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID")
        ),
        lines=tuple(_line(node, names["quantity"]) for node in R.findall(root, names["line"])),
        vat_breakdown=tuple(
            _vat_breakdown(node) for node in R.findall(root, "cac:TaxTotal/cac:TaxSubtotal")
        ),
        line_net_total=R.amount(totals, "cbc:LineExtensionAmount", "BT-106 sum of line net"),
        net_total=R.amount(totals, "cbc:TaxExclusiveAmount", "BT-109 total without VAT"),
        vat_total=_vat_total(root, currency),
        gross_total=R.amount(totals, "cbc:TaxInclusiveAmount", "BT-112 total with VAT"),
        amount_due=R.amount(totals, "cbc:PayableAmount", "BT-115 amount due"),
        source_format=DocumentFormat.UBL,
    )


def _po_ref(root: etree._Element) -> str | None:
    """BT-13. UBL makes OrderReference/ID mandatory once a sales order number
    (BT-14) is given, so senders without a purchase order write "NA" there."""
    value = R.text(root, "cac:OrderReference/cbc:ID")
    has_sales_order = R.text(root, "cac:OrderReference/cbc:SalesOrderID") is not None
    return None if value == "NA" and has_sales_order else value


def _party(root: etree._Element, path: str, name_field: str) -> Party:
    node = R.find(root, path)
    if node is None:
        raise InvoiceParseError(f"missing party ({path})")
    vat_id = None
    for scheme in R.findall(node, "cac:PartyTaxScheme"):
        if R.text(scheme, "cac:TaxScheme/cbc:ID") == "VAT":
            vat_id = compact_id(R.text(scheme, "cbc:CompanyID"))
    return Party(
        name=R.required(node, "cac:PartyLegalEntity/cbc:RegistrationName", name_field),
        vat_id=vat_id,
    )


def _vat_total(root: etree._Element, currency: str) -> str | None:
    """BT-110. A second TaxTotal may carry the VAT in accounting currency (BT-111)."""
    amounts = R.findall(root, "cac:TaxTotal/cbc:TaxAmount")
    for node in amounts:
        if node.get("currencyID") == currency:
            return (node.text or "").strip()
    return (amounts[0].text or "").strip() if amounts else None


def _vat_breakdown(node: etree._Element) -> VatBreakdown:
    return VatBreakdown(
        category=R.required(node, "cac:TaxCategory/cbc:ID", "BT-118 VAT category"),
        rate=R.optional_amount(node, "cac:TaxCategory/cbc:Percent", "BT-119 VAT rate"),
        taxable_amount=R.amount(node, "cbc:TaxableAmount", "BT-116 taxable amount"),
        tax_amount=R.amount(node, "cbc:TaxAmount", "BT-117 tax amount"),
    )


def _line(node: etree._Element, quantity_path: str) -> Line:
    return Line(
        line_id=R.required(node, "cbc:ID", "BT-126 line id"),
        description=R.required(node, "cac:Item/cbc:Name", "BT-153 item name"),
        quantity=R.amount(node, quantity_path, "BT-129 quantity"),
        unit_price=R.amount(node, "cac:Price/cbc:PriceAmount", "BT-146 net price"),
        net=R.amount(node, "cbc:LineExtensionAmount", "BT-131 line net amount"),
        vat_rate=R.optional_amount(
            node, "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent", "BT-152 line VAT rate"
        ),
    )
