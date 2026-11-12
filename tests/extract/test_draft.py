import pytest
from pydantic import ValidationError

from app.extract import InvoiceDraft
from app.models import Invoice
from tests.models.test_invoice import invoice_data


def test_empty_draft_is_allowed():
    assert InvoiceDraft().number is None


def test_complete_draft_becomes_an_invoice():
    draft = InvoiceDraft.model_validate(invoice_data())
    assert draft.to_invoice() == Invoice.model_validate(invoice_data())


def test_draft_missing_a_mandatory_field_does_not_become_an_invoice():
    data = invoice_data()
    del data["gross_total"]
    with pytest.raises(ValidationError):
        InvoiceDraft.model_validate(data).to_invoice()


def test_draft_rejects_float_amounts_like_the_invoice_does():
    with pytest.raises(ValidationError, match="not float"):
        InvoiceDraft(gross_total=119.0)


def test_draft_line_may_be_partial():
    draft = InvoiceDraft.model_validate({"lines": [{"description": "Beratung"}]})
    assert (draft.lines[0].description, draft.lines[0].net) == ("Beratung", None)
