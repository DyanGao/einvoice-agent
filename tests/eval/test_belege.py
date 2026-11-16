"""Scoring on the Belege labels. The label below is written for this test in
the Belege schema; none of the dataset's files are in the repository."""

from app.extract import InvoiceDraft
from eval.belege import expected, summarize, wrong


def field(text):
    return {"text": text, "box": [0, 0, 1, 1]}


def line(quantity, unit_price, total):
    return {
        "description": field("Beratung"),
        "quantity": {**field(str(quantity)), "value": quantity},
        "unit_price": {**field(str(unit_price)), "value": unit_price},
        "line_total": {**field(str(total)), "value": total},
    }


def label(document_type="Rechnung", sign=1, due=True, vat_id=True):
    fields = {
        "supplier_name": field("Muster  Werkstatt GmbH"),
        "customer_name": field("Beispiel AG"),
        "invoice_number": field("RG-1"),
        "invoice_date": field("01.08.2024"),
        "iban": field("DE42 9999 9999 7388 7219 73"),
    }
    if due:
        fields["due_date"] = field("15.08.2024")
    if vat_id:
        fields["vat_id"] = field("DE123456789")
    return {
        "document_type": document_type,
        "currency": "EUR",
        "fields": fields,
        "line_items": [line(sign * 2, 50.0, sign * 100.0)],
        "totals": {"net": sign * 100.0, "vat": sign * 19.0, "gross": sign * 119.0},
    }


def draft(**changes):
    values = {
        "number": "RG-1",
        "issue_date": "2024-08-01",
        "due_date": "2024-08-15",
        "type_code": "380",
        "currency": "EUR",
        "seller": {"name": "Muster Werkstatt GmbH", "vat_id": "DE123456789"},
        "buyer": {"name": "Beispiel AG"},
        "payment_ibans": ["DE42999999997388721973"],
        "lines": [{"quantity": "2", "unit_price": "50.00", "net": "100.00"}],
        "net_total": "100.00",
        "vat_total": "19.00",
        "gross_total": "119.00",
    }
    return InvoiceDraft.model_validate(values | changes)


def test_correct_reading_has_no_wrong_field():
    assert wrong(label(), draft()) == ([], 16)


def test_wrong_total_is_reported_by_name():
    assert wrong(label(), draft(gross_total="118.00"))[0] == ["gross_total"]


def test_credit_note_amounts_count_unsigned_or_signed():
    note = label("Gutschrift", sign=-1)
    unsigned = draft(type_code="381")
    signed = draft(
        type_code="381",
        net_total="-100.00",
        lines=[{"quantity": "-2", "unit_price": "50.00", "net": "-100.00"}],
    )
    assert wrong(note, unsigned)[0] == []
    assert wrong(note, signed)[0] == []


def test_fields_the_label_lacks_are_not_scored():
    assert wrong(label(due=False, vat_id=False), draft(due_date=None))[1] == 14


def test_missing_line_counts_its_fields_wrong():
    misses, _ = wrong(label(), draft(lines=[]))
    assert misses == ["line_count", "lines.0.quantity", "lines.0.unit_price", "lines.0.net"]


def test_no_answer_gets_every_field_wrong():
    misses, compared = wrong(label(), None)
    assert len(misses) == compared


def test_expected_values_ignore_spacing_and_case_in_names_and_codes():
    values = expected(label())
    assert (values["seller.name"], values["payment_iban"]) == (
        "muster werkstatt gmbh",
        "DE42999999997388721973",
    )


def test_summary_counts_fields_documents_and_groups():
    results = [
        {
            "variant": "scan",
            "vat_scheme": "regelbesteuert",
            "compared": 10,
            "wrong": [],
            "error": None,
        },
        {
            "variant": "photo",
            "vat_scheme": "kleinunternehmer",
            "compared": 10,
            "wrong": ["vat_total", "lines.0.net"],
            "error": None,
        },
    ]
    summary = summarize(results)
    assert summary["overall"]["field_accuracy"] == 0.9
    assert summary["overall"]["document_exact_match"] == 0.5
    assert summary["by"]["variant:photo"]["field_accuracy"] == 0.8
    assert summary["wrong_by_field"] == {"vat_total": 1, "lines": 1}
