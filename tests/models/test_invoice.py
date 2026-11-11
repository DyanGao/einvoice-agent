from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import DocumentFormat, Invoice, Line, Party


def invoice_data(**overrides):
    data = {
        "number": "RE-2026-001",
        "issue_date": "2026-11-11",
        "type_code": "380",
        "currency": "EUR",
        "seller": {"name": "Muster Lieferant GmbH", "vat_id": "DE123456789"},
        "buyer": {"name": "Beispiel Kunde AG"},
        "lines": [
            {
                "line_id": "1",
                "description": "Beratung",
                "quantity": "2",
                "unit_price": "50.00",
                "net": "100.00",
                "vat_rate": "19",
            }
        ],
        "line_net_total": "100.00",
        "net_total": "100.00",
        "vat_total": "19.00",
        "gross_total": "119.00",
        "amount_due": "119.00",
        "source_format": "ubl",
    }
    data.update(overrides)
    return data


def test_amounts_from_strings_are_exact_decimals():
    invoice = Invoice.model_validate(invoice_data(net_total="0.10", gross_total="0.20"))
    assert invoice.net_total + invoice.gross_total == Decimal("0.30")


def test_float_amount_is_rejected():
    with pytest.raises(ValidationError, match="not float"):
        Invoice.model_validate(invoice_data(gross_total=119.0))


def test_float_amount_in_line_is_rejected():
    with pytest.raises(ValidationError, match="not float"):
        Line(line_id="1", description="x", quantity=1.5, unit_price="1", net="1.5")


def test_dates_are_parsed():
    assert Invoice.model_validate(invoice_data()).issue_date == date(2026, 11, 11)


def test_optional_fields_default_to_none():
    invoice = Invoice.model_validate(invoice_data())
    assert (invoice.due_date, invoice.po_ref, invoice.payment_iban) == (None, None, None)


def test_inconsistent_totals_are_still_representable():
    invoice = Invoice.model_validate(invoice_data(gross_total="999.00"))
    assert invoice.gross_total == Decimal("999.00")


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        Invoice.model_validate(invoice_data(surprise="x"))


def test_invoice_is_immutable():
    invoice = Invoice.model_validate(invoice_data())
    with pytest.raises(ValidationError):
        invoice.number = "other"


def test_type_381_is_a_credit_note():
    assert Invoice.model_validate(invoice_data(type_code="381")).is_credit_note


def test_type_380_is_not_a_credit_note():
    assert not Invoice.model_validate(invoice_data()).is_credit_note


def test_currency_must_be_three_letters():
    with pytest.raises(ValidationError):
        Invoice.model_validate(invoice_data(currency="EURO"))


def test_structured_formats_are_flagged():
    structured = {f for f in DocumentFormat if f.is_structured}
    assert structured == {DocumentFormat.UBL, DocumentFormat.CII, DocumentFormat.ZUGFERD_PDF}


def test_party_requires_a_name():
    with pytest.raises(ValidationError):
        Party()
