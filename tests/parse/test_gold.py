"""Parser output against gold values typed in by hand from reading the XML.

The gold is deliberately not produced by any code in this repo.
"""

from datetime import date
from decimal import Decimal as D

import pytest

from app.models import DocumentFormat, Invoice, Line, Party, VatBreakdown
from app.parse import parse_invoice
from tests.parse import samples

GOLD_01_01A = {
    "number": "123456XX",
    "issue_date": date(2016, 4, 4),
    "type_code": "380",
    "currency": "EUR",
    "due_date": None,
    "buyer_reference": "04011000-12345-03",
    "po_ref": None,
    "seller": Party(name="[Seller name]", vat_id="DE123456789"),
    "buyer": Party(name="[Buyer name]", vat_id=None),
    "payment_ibans": ("DE79000000001234567890",),
    "lines": (
        Line(
            line_id="Zeitschrift [...]",
            description="Zeitschrift [...]",
            quantity=D("1"),
            unit_price=D("288.79"),
            net=D("288.79"),
            vat_rate=D("7"),
        ),
        Line(
            line_id="Porto + Versandkosten",
            description="Porto + Versandkosten",
            quantity=D("1"),
            unit_price=D("26.07"),
            net=D("26.07"),
            vat_rate=D("7"),
        ),
    ),
    "vat_breakdown": (
        VatBreakdown(category="S", rate=D("7"), taxable_amount=D("314.86"), tax_amount=D("22.04")),
    ),
    "line_net_total": D("314.86"),
    "net_total": D("314.86"),
    "vat_total": D("22.04"),
    "gross_total": D("336.9"),
    "amount_due": D("336.9"),
}

GOLD_03_06A = {
    "number": "112233",
    "issue_date": date(2021, 4, 23),
    "type_code": "380",
    "currency": "EUR",
    "due_date": date(2021, 4, 28),
    "buyer_reference": "991-33333TEST-33",
    "po_ref": None,
    "seller": Party(name="Testverkäufer", vat_id="DE12345464867"),
    "buyer": Party(name="Testkäufer", vat_id=None),
    "payment_ibans": ("DE79000000001234567890",),
    "lines": (
        Line(
            line_id="1",
            description="Neumotor",
            quantity=D("1"),
            unit_price=D("1000"),
            net=D("1000"),
            vat_rate=D("19"),
        ),
        Line(
            line_id="2",
            description="Bemessungsgrundlage und Umsatzsteuer auf Altteil",
            quantity=D("1"),
            unit_price=D("100"),
            net=D("100"),
            vat_rate=D("19"),
        ),
        Line(
            line_id="3",
            description="Korrektur/Stornierung Bemessungsgrundlage der Umsatzsteuer auf Altteil",
            quantity=D("-1"),
            unit_price=D("100"),
            net=D("-100"),
            vat_rate=D("0"),
        ),
        Line(
            line_id="4",
            description="Montage",
            quantity=D("1"),
            unit_price=D("500"),
            net=D("500"),
            vat_rate=D("19"),
        ),
    ),
    "vat_breakdown": (
        VatBreakdown(category="S", rate=D("19"), taxable_amount=D("1600"), tax_amount=D("304")),
        VatBreakdown(category="Z", rate=D("0"), taxable_amount=D("-100"), tax_amount=D("0")),
    ),
    "line_net_total": D("1500"),
    "net_total": D("1500"),
    "vat_total": D("304"),
    "gross_total": D("1804"),
    "amount_due": D("1804"),
}

CASES = [
    pytest.param(samples.ubl("01.01a"), DocumentFormat.UBL, GOLD_01_01A, id="01.01a-ubl"),
    pytest.param(samples.cii("01.01a"), DocumentFormat.CII, GOLD_01_01A, id="01.01a-cii"),
    pytest.param(samples.ubl("03.06a"), DocumentFormat.UBL, GOLD_03_06A, id="03.06a-ubl"),
    pytest.param(samples.cii("03.06a"), DocumentFormat.CII, GOLD_03_06A, id="03.06a-cii"),
]


@pytest.mark.parametrize(("xml", "source_format", "gold"), CASES)
def test_parsed_invoice_equals_hand_written_gold(xml, source_format, gold):
    assert parse_invoice(xml) == Invoice(source_format=source_format, **gold)
