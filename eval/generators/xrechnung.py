"""Render a canonical invoice as UBL 2.1 and as UN/CEFACT CII.

Written as explicit templates so the output can be read next to the official
samples in tests/fixtures/xrechnung-testsuite. Element order follows the XSDs.
"""

from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

from eval.generators.canonical import GUIDELINE_IDS, CanonicalInvoice, Company

PAYMENT_TERMS_WITHOUT_DUE_DATE = "Zahlbar sofort ohne Abzug."
CREDIT_TRANSFER = "58"


def _x(value: object) -> str:
    return escape(str(value))


def _amount(value: Decimal) -> str:
    return f"{value:.2f}"


def _opt(value: object | None, template: str) -> str:
    return "" if value is None else template.format(_x(value))


# --------------------------------------------------------------------- UBL


def _ubl_party(company: Company, *, seller: bool) -> str:
    tax_scheme = _opt(
        company.vat_id,
        "<cac:PartyTaxScheme><cbc:CompanyID>{}</cbc:CompanyID>"
        "<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>",
    )
    contact = ""
    if seller:
        contact = (
            f"<cac:Contact><cbc:Name>{_x(company.contact_name)}</cbc:Name>"
            + _opt(company.phone, "<cbc:Telephone>{}</cbc:Telephone>")
            + f"<cbc:ElectronicMail>{_x(company.email)}</cbc:ElectronicMail></cac:Contact>"
        )
    return (
        "<cac:Party>"
        f'<cbc:EndpointID schemeID="EM">{_x(company.email)}</cbc:EndpointID>'
        "<cac:PostalAddress>"
        f"<cbc:StreetName>{_x(company.street)}</cbc:StreetName>"
        f"<cbc:CityName>{_x(company.city)}</cbc:CityName>"
        f"<cbc:PostalZone>{_x(company.postcode)}</cbc:PostalZone>"
        "<cac:Country><cbc:IdentificationCode>DE</cbc:IdentificationCode></cac:Country>"
        "</cac:PostalAddress>"
        f"{tax_scheme}"
        "<cac:PartyLegalEntity>"
        f"<cbc:RegistrationName>{_x(company.name)}</cbc:RegistrationName>"
        "</cac:PartyLegalEntity>"
        f"{contact}"
        "</cac:Party>"
    )


def _ubl_amount(tag: str, value: Decimal, currency_attr: str) -> str:
    return f"<cbc:{tag} {currency_attr}>{_amount(value)}</cbc:{tag}>"


def _ubl_tax_category(tag: str, rate: Decimal) -> str:
    return (
        f"<cac:{tag}><cbc:ID>S</cbc:ID><cbc:Percent>{rate}</cbc:Percent>"
        f"<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:{tag}>"
    )


# A UBL credit note is a document of its own, with its own names for these.
_UBL = {
    "380": ("Invoice", "InvoiceTypeCode", "InvoiceLine", "InvoicedQuantity"),
    "381": ("CreditNote", "CreditNoteTypeCode", "CreditNoteLine", "CreditedQuantity"),
}


def render_ubl(invoice: CanonicalInvoice, profile: str) -> bytes:
    root, type_tag, line_tag, quantity_tag = _UBL.get(invoice.type_code, _UBL["380"])
    cur = f'currencyID="{invoice.currency}"'
    payment_means = "".join(
        f"<cac:PaymentMeans><cbc:PaymentMeansCode>{CREDIT_TRANSFER}</cbc:PaymentMeansCode>"
        f"<cac:PayeeFinancialAccount><cbc:ID>{iban}</cbc:ID></cac:PayeeFinancialAccount>"
        "</cac:PaymentMeans>"
        for iban in invoice.ibans
    )
    payment_terms = (
        ""
        if invoice.due_date
        else f"<cac:PaymentTerms><cbc:Note>{PAYMENT_TERMS_WITHOUT_DUE_DATE}</cbc:Note>"
        "</cac:PaymentTerms>"
    )
    subtotals = "".join(
        "<cac:TaxSubtotal>"
        f"<cbc:TaxableAmount {cur}>{_amount(group.taxable_amount)}</cbc:TaxableAmount>"
        f"<cbc:TaxAmount {cur}>{_amount(group.tax_amount)}</cbc:TaxAmount>"
        f"{_ubl_tax_category('TaxCategory', group.rate)}"
        "</cac:TaxSubtotal>"
        for group in invoice.tax_groups
    )
    lines = "".join(
        f"<cac:{line_tag}>"
        f"<cbc:ID>{_x(item.line_id)}</cbc:ID>"
        f'<cbc:{quantity_tag} unitCode="{item.unit_code}">{item.quantity}</cbc:{quantity_tag}>'
        f"{_ubl_amount('LineExtensionAmount', item.net, cur)}"
        f"<cac:Item><cbc:Name>{_x(item.name)}</cbc:Name>"
        f"{_ubl_tax_category('ClassifiedTaxCategory', item.vat_rate)}</cac:Item>"
        f"<cac:Price>{_ubl_amount('PriceAmount', item.unit_price, cur)}</cac:Price>"
        f"</cac:{line_tag}>"
        for item in invoice.items
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<ubl:{root} xmlns:ubl="urn:oasis:names:specification:ubl:schema:xsd:{root}-2"'
        ' xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"'
        ' xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
        f"<cbc:CustomizationID>{GUIDELINE_IDS[profile]}</cbc:CustomizationID>"
        "<cbc:ProfileID>urn:fdc:peppol.eu:2017:poacc:billing:01:1.0</cbc:ProfileID>"
        f"<cbc:ID>{_x(invoice.number)}</cbc:ID>"
        f"<cbc:IssueDate>{invoice.issue_date.isoformat()}</cbc:IssueDate>"
        # A credit note has no header due date in UBL; the generator gives it none.
        + _opt(invoice.due_date and invoice.due_date.isoformat(), "<cbc:DueDate>{}</cbc:DueDate>")
        + f"<cbc:{type_tag}>{invoice.type_code}</cbc:{type_tag}>"
        f"<cbc:DocumentCurrencyCode>{invoice.currency}</cbc:DocumentCurrencyCode>"
        + _opt(invoice.buyer_reference, "<cbc:BuyerReference>{}</cbc:BuyerReference>")
        + _opt(invoice.po_ref, "<cac:OrderReference><cbc:ID>{}</cbc:ID></cac:OrderReference>")
        + _opt(
            invoice.preceding_ref,
            "<cac:BillingReference><cac:InvoiceDocumentReference><cbc:ID>{}</cbc:ID>"
            "</cac:InvoiceDocumentReference></cac:BillingReference>",
        )
        + "<cac:AccountingSupplierParty>"
        f"{_ubl_party(invoice.seller, seller=True)}</cac:AccountingSupplierParty>"
        "<cac:AccountingCustomerParty>"
        f"{_ubl_party(invoice.buyer, seller=False)}</cac:AccountingCustomerParty>"
        "<cac:Delivery>"
        f"<cbc:ActualDeliveryDate>{invoice.issue_date.isoformat()}</cbc:ActualDeliveryDate>"
        "</cac:Delivery>"
        f"{payment_means}{payment_terms}"
        f"<cac:TaxTotal><cbc:TaxAmount {cur}>{_amount(invoice.vat_total)}</cbc:TaxAmount>"
        f"{subtotals}</cac:TaxTotal>"
        "<cac:LegalMonetaryTotal>"
        + _ubl_amount("LineExtensionAmount", invoice.line_net_total, cur)
        + _ubl_amount("TaxExclusiveAmount", invoice.net_total, cur)
        + _ubl_amount("TaxInclusiveAmount", invoice.gross_total, cur)
        + _ubl_amount("PayableAmount", invoice.amount_due, cur)
        + "</cac:LegalMonetaryTotal>"
        f"{lines}"
        f"</ubl:{root}>\n"
    )
    return xml.encode("utf-8")


# --------------------------------------------------------------------- CII


def _cii_date(value: date) -> str:
    return f'<udt:DateTimeString format="102">{value:%Y%m%d}</udt:DateTimeString>'


def _cii_party(tag: str, company: Company, *, seller: bool) -> str:
    contact = ""
    if seller:
        contact = (
            f"<ram:DefinedTradeContact><ram:PersonName>{_x(company.contact_name)}</ram:PersonName>"
            + _opt(
                company.phone,
                "<ram:TelephoneUniversalCommunication><ram:CompleteNumber>{}"
                "</ram:CompleteNumber></ram:TelephoneUniversalCommunication>",
            )
            + f"<ram:EmailURIUniversalCommunication><ram:URIID>{_x(company.email)}</ram:URIID>"
            "</ram:EmailURIUniversalCommunication></ram:DefinedTradeContact>"
        )
    return (
        f"<ram:{tag}>"
        f"<ram:Name>{_x(company.name)}</ram:Name>"
        f"{contact}"
        "<ram:PostalTradeAddress>"
        f"<ram:PostcodeCode>{_x(company.postcode)}</ram:PostcodeCode>"
        f"<ram:LineOne>{_x(company.street)}</ram:LineOne>"
        f"<ram:CityName>{_x(company.city)}</ram:CityName>"
        "<ram:CountryID>DE</ram:CountryID>"
        "</ram:PostalTradeAddress>"
        f'<ram:URIUniversalCommunication><ram:URIID schemeID="EM">{_x(company.email)}</ram:URIID>'
        "</ram:URIUniversalCommunication>"
        + _opt(
            company.vat_id,
            '<ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">{}</ram:ID>'
            "</ram:SpecifiedTaxRegistration>",
        )
        + f"</ram:{tag}>"
    )


def render_cii(invoice: CanonicalInvoice, profile: str) -> bytes:
    lines = "".join(
        "<ram:IncludedSupplyChainTradeLineItem>"
        "<ram:AssociatedDocumentLineDocument>"
        f"<ram:LineID>{_x(item.line_id)}</ram:LineID></ram:AssociatedDocumentLineDocument>"
        f"<ram:SpecifiedTradeProduct><ram:Name>{_x(item.name)}</ram:Name>"
        "</ram:SpecifiedTradeProduct>"
        "<ram:SpecifiedLineTradeAgreement><ram:NetPriceProductTradePrice>"
        f"<ram:ChargeAmount>{_amount(item.unit_price)}</ram:ChargeAmount>"
        "</ram:NetPriceProductTradePrice></ram:SpecifiedLineTradeAgreement>"
        "<ram:SpecifiedLineTradeDelivery>"
        f'<ram:BilledQuantity unitCode="{item.unit_code}">{item.quantity}</ram:BilledQuantity>'
        "</ram:SpecifiedLineTradeDelivery>"
        "<ram:SpecifiedLineTradeSettlement>"
        "<ram:ApplicableTradeTax><ram:TypeCode>VAT</ram:TypeCode>"
        "<ram:CategoryCode>S</ram:CategoryCode>"
        f"<ram:RateApplicablePercent>{item.vat_rate}</ram:RateApplicablePercent>"
        "</ram:ApplicableTradeTax>"
        "<ram:SpecifiedTradeSettlementLineMonetarySummation>"
        f"<ram:LineTotalAmount>{_amount(item.net)}</ram:LineTotalAmount>"
        "</ram:SpecifiedTradeSettlementLineMonetarySummation>"
        "</ram:SpecifiedLineTradeSettlement>"
        "</ram:IncludedSupplyChainTradeLineItem>"
        for item in invoice.items
    )
    payment_means = "".join(
        "<ram:SpecifiedTradeSettlementPaymentMeans>"
        f"<ram:TypeCode>{CREDIT_TRANSFER}</ram:TypeCode>"
        f"<ram:PayeePartyCreditorFinancialAccount><ram:IBANID>{iban}</ram:IBANID>"
        "</ram:PayeePartyCreditorFinancialAccount>"
        "</ram:SpecifiedTradeSettlementPaymentMeans>"
        for iban in invoice.ibans
    )
    taxes = "".join(
        "<ram:ApplicableTradeTax>"
        f"<ram:CalculatedAmount>{_amount(group.tax_amount)}</ram:CalculatedAmount>"
        "<ram:TypeCode>VAT</ram:TypeCode>"
        f"<ram:BasisAmount>{_amount(group.taxable_amount)}</ram:BasisAmount>"
        "<ram:CategoryCode>S</ram:CategoryCode>"
        f"<ram:RateApplicablePercent>{group.rate}</ram:RateApplicablePercent>"
        "</ram:ApplicableTradeTax>"
        for group in invoice.tax_groups
    )
    if invoice.due_date:
        terms = f"<ram:DueDateDateTime>{_cii_date(invoice.due_date)}</ram:DueDateDateTime>"
    else:
        terms = f"<ram:Description>{PAYMENT_TERMS_WITHOUT_DUE_DATE}</ram:Description>"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<rsm:CrossIndustryInvoice"
        ' xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"'
        ' xmlns:ram="urn:un:unece:uncefact:data:standard:'
        'ReusableAggregateBusinessInformationEntity:100"'
        ' xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">'
        "<rsm:ExchangedDocumentContext>"
        "<ram:BusinessProcessSpecifiedDocumentContextParameter>"
        "<ram:ID>urn:fdc:peppol.eu:2017:poacc:billing:01:1.0</ram:ID>"
        "</ram:BusinessProcessSpecifiedDocumentContextParameter>"
        "<ram:GuidelineSpecifiedDocumentContextParameter>"
        f"<ram:ID>{GUIDELINE_IDS[profile]}</ram:ID>"
        "</ram:GuidelineSpecifiedDocumentContextParameter>"
        "</rsm:ExchangedDocumentContext>"
        "<rsm:ExchangedDocument>"
        f"<ram:ID>{_x(invoice.number)}</ram:ID>"
        f"<ram:TypeCode>{invoice.type_code}</ram:TypeCode>"
        f"<ram:IssueDateTime>{_cii_date(invoice.issue_date)}</ram:IssueDateTime>"
        "</rsm:ExchangedDocument>"
        "<rsm:SupplyChainTradeTransaction>"
        f"{lines}"
        "<ram:ApplicableHeaderTradeAgreement>"
        + _opt(invoice.buyer_reference, "<ram:BuyerReference>{}</ram:BuyerReference>")
        + _cii_party("SellerTradeParty", invoice.seller, seller=True)
        + _cii_party("BuyerTradeParty", invoice.buyer, seller=False)
        + _opt(
            invoice.po_ref,
            "<ram:BuyerOrderReferencedDocument><ram:IssuerAssignedID>{}</ram:IssuerAssignedID>"
            "</ram:BuyerOrderReferencedDocument>",
        )
        + "</ram:ApplicableHeaderTradeAgreement>"
        "<ram:ApplicableHeaderTradeDelivery><ram:ActualDeliverySupplyChainEvent>"
        f"<ram:OccurrenceDateTime>{_cii_date(invoice.issue_date)}</ram:OccurrenceDateTime>"
        "</ram:ActualDeliverySupplyChainEvent></ram:ApplicableHeaderTradeDelivery>"
        "<ram:ApplicableHeaderTradeSettlement>"
        f"<ram:InvoiceCurrencyCode>{invoice.currency}</ram:InvoiceCurrencyCode>"
        f"{payment_means}{taxes}"
        f"<ram:SpecifiedTradePaymentTerms>{terms}</ram:SpecifiedTradePaymentTerms>"
        "<ram:SpecifiedTradeSettlementHeaderMonetarySummation>"
        f"<ram:LineTotalAmount>{_amount(invoice.line_net_total)}</ram:LineTotalAmount>"
        f"<ram:TaxBasisTotalAmount>{_amount(invoice.net_total)}</ram:TaxBasisTotalAmount>"
        f'<ram:TaxTotalAmount currencyID="{invoice.currency}">'
        f"{_amount(invoice.vat_total)}</ram:TaxTotalAmount>"
        f"<ram:GrandTotalAmount>{_amount(invoice.gross_total)}</ram:GrandTotalAmount>"
        f"<ram:DuePayableAmount>{_amount(invoice.amount_due)}</ram:DuePayableAmount>"
        "</ram:SpecifiedTradeSettlementHeaderMonetarySummation>"
        + _opt(
            invoice.preceding_ref,
            "<ram:InvoiceReferencedDocument><ram:IssuerAssignedID>{}</ram:IssuerAssignedID>"
            "</ram:InvoiceReferencedDocument>",
        )
        + "</ram:ApplicableHeaderTradeSettlement>"
        "</rsm:SupplyChainTradeTransaction>"
        "</rsm:CrossIndustryInvoice>\n"
    )
    return xml.encode("utf-8")
