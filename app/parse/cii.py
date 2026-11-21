"""UN/CEFACT Cross Industry Invoice (D16B) → Invoice (EN 16931 subset)."""

from lxml import etree

from app.models import DocumentFormat, Invoice, Line, Party, VatBreakdown
from app.parse.fields import InvoiceParseError, Reader, compact_id, compact_ids, to_date

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}
R = Reader(NS)
CII_DATE = "%Y%m%d"  # format code 102, the only one EN 16931 allows


def parse_cii(root: etree._Element) -> Invoice:
    document = _child(root, "rsm:ExchangedDocument")
    transaction = _child(root, "rsm:SupplyChainTradeTransaction")
    agreement = _child(transaction, "ram:ApplicableHeaderTradeAgreement")
    settlement = _child(transaction, "ram:ApplicableHeaderTradeSettlement")
    totals = _child(settlement, "ram:SpecifiedTradeSettlementHeaderMonetarySummation")
    currency = R.required(settlement, "ram:InvoiceCurrencyCode", "BT-5 currency")
    due_date = R.text(
        settlement, "ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString"
    )

    return Invoice(
        number=R.required(document, "ram:ID", "BT-1 invoice number"),
        issue_date=to_date(
            R.required(document, "ram:IssueDateTime/udt:DateTimeString", "BT-2 issue date"),
            CII_DATE,
            "BT-2 issue date",
        ),
        type_code=R.required(document, "ram:TypeCode", "BT-3 type code"),
        currency=currency,
        due_date=None if due_date is None else to_date(due_date, CII_DATE, "BT-9 due date"),
        buyer_reference=R.text(agreement, "ram:BuyerReference"),
        po_ref=R.text(agreement, "ram:BuyerOrderReferencedDocument/ram:IssuerAssignedID"),
        preceding_ref=R.text(settlement, "ram:InvoiceReferencedDocument/ram:IssuerAssignedID"),
        seller=_party(agreement, "ram:SellerTradeParty", "BT-27 seller name"),
        buyer=_party(agreement, "ram:BuyerTradeParty", "BT-44 buyer name"),
        payment_ibans=compact_ids(
            node.text
            for node in R.findall(
                settlement,
                "ram:SpecifiedTradeSettlementPaymentMeans"
                "/ram:PayeePartyCreditorFinancialAccount/ram:IBANID",
            )
        ),
        lines=tuple(
            _line(node) for node in R.findall(transaction, "ram:IncludedSupplyChainTradeLineItem")
        ),
        vat_breakdown=tuple(
            _vat_breakdown(node) for node in R.findall(settlement, "ram:ApplicableTradeTax")
        ),
        line_net_total=R.amount(totals, "ram:LineTotalAmount", "BT-106 sum of line net"),
        net_total=R.amount(totals, "ram:TaxBasisTotalAmount", "BT-109 total without VAT"),
        vat_total=_vat_total(totals, currency),
        gross_total=R.amount(totals, "ram:GrandTotalAmount", "BT-112 total with VAT"),
        amount_due=R.amount(totals, "ram:DuePayableAmount", "BT-115 amount due"),
        source_format=DocumentFormat.CII,
    )


def _child(node: etree._Element, path: str) -> etree._Element:
    child = R.find(node, path)
    if child is None:
        raise InvoiceParseError(f"missing {path}")
    return child


def _party(agreement: etree._Element, path: str, name_field: str) -> Party:
    node = _child(agreement, path)
    vat_id = None
    for registration in R.findall(node, "ram:SpecifiedTaxRegistration/ram:ID"):
        if registration.get("schemeID") == "VA":
            vat_id = compact_id(registration.text)
    return Party(name=R.required(node, "ram:Name", name_field), vat_id=vat_id)


def _vat_total(totals: etree._Element, currency: str) -> str | None:
    """BT-110. A second TaxTotalAmount may carry the VAT in accounting currency (BT-111)."""
    amounts = R.findall(totals, "ram:TaxTotalAmount")
    for node in amounts:
        if node.get("currencyID") == currency:
            return (node.text or "").strip()
    return (amounts[0].text or "").strip() if amounts else None


def _vat_breakdown(node: etree._Element) -> VatBreakdown:
    return VatBreakdown(
        category=R.required(node, "ram:CategoryCode", "BT-118 VAT category"),
        rate=R.optional_amount(node, "ram:RateApplicablePercent", "BT-119 VAT rate"),
        taxable_amount=R.amount(node, "ram:BasisAmount", "BT-116 taxable amount"),
        tax_amount=R.amount(node, "ram:CalculatedAmount", "BT-117 tax amount"),
    )


def _line(node: etree._Element) -> Line:
    settlement = _child(node, "ram:SpecifiedLineTradeSettlement")
    return Line(
        line_id=R.required(node, "ram:AssociatedDocumentLineDocument/ram:LineID", "BT-126 line id"),
        description=R.required(node, "ram:SpecifiedTradeProduct/ram:Name", "BT-153 item name"),
        quantity=R.amount(
            node, "ram:SpecifiedLineTradeDelivery/ram:BilledQuantity", "BT-129 quantity"
        ),
        unit_price=R.amount(
            node,
            "ram:SpecifiedLineTradeAgreement/ram:NetPriceProductTradePrice/ram:ChargeAmount",
            "BT-146 net price",
        ),
        net=R.amount(
            settlement,
            "ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount",
            "BT-131 line net amount",
        ),
        vat_rate=R.optional_amount(
            settlement, "ram:ApplicableTradeTax/ram:RateApplicablePercent", "BT-152 line VAT rate"
        ),
    )
