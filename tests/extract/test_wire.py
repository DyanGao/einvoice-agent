from datetime import date
from decimal import Decimal

import pytest

from app.extract.wire import WireInvoice, to_draft
from app.models import DocumentFormat
from tests.extract.fakes import WIRE


def draft(**overrides):
    return to_draft(WireInvoice.model_validate({**WIRE, **overrides}), DocumentFormat.PDF_TEXT)


def test_strings_become_exact_amounts_and_dates():
    result = draft()
    assert (result.gross_total, result.issue_date, result.lines[0].quantity) == (
        Decimal("297.50"),
        date(2026, 3, 5),
        Decimal("2.5"),
    )


def test_identifiers_are_compacted_and_text_is_trimmed():
    result = draft()
    assert (result.seller.vat_id, result.currency, result.po_ref) == (
        "DE123456789",
        "EUR",
        "PO-123456",
    )


def test_the_same_iban_written_twice_is_kept_once():
    assert draft().payment_ibans == ("DE79000000001234567890",)


@pytest.mark.parametrize("value", ["1.234,56", "1,234.56", "297.50 EUR", "viel", "", "NaN", "1e3"])
def test_amount_not_in_plain_decimal_form_becomes_missing_not_guessed(value):
    assert draft(gross_total=value).gross_total is None


@pytest.mark.parametrize("value", ["05.03.2026", "2026-02-31", "morgen", ""])
def test_date_not_in_iso_form_becomes_missing(value):
    assert draft(issue_date=value).issue_date is None


def test_empty_strings_become_missing():
    assert (draft(number="  ").number, draft(seller_vat_id=" ").seller.vat_id) == (None, None)


def test_draft_carries_the_document_format():
    assert draft().source_format is DocumentFormat.PDF_TEXT


def test_complete_extraction_becomes_an_invoice():
    assert draft().to_invoice().number == "RE-2026-00042"


def test_unknown_key_from_the_model_is_rejected():
    with pytest.raises(ValueError, match="surprise"):
        WireInvoice.model_validate({**WIRE, "surprise": "x"})


def test_schema_satisfies_structured_output_rules():
    unsupported = {"minimum", "maximum", "minLength", "maxLength", "pattern", "multipleOf"}

    def check(node, path="schema"):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, path
                assert set(node["required"]) == set(node["properties"]), path
            assert not unsupported & set(node), path
            for key, child in node.items():
                check(child, f"{path}/{key}")
        elif isinstance(node, list):
            for child in node:
                check(child, path)

    check(WireInvoice.model_json_schema())
