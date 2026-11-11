import copy

import pytest

from eval.generators.structured import generate
from eval.score import score, validation_correct, wrong_fields

DOCUMENT = next(generate(1))
GOLD = DOCUMENT.gold_record()
FIELDS_PER_LINE = 6
HEADER_FIELDS = 18  # 12 scalar header fields + 2x2 party fields + ibans + source_format
FIELDS_PER_VAT_GROUP = 4


def prediction(**overrides) -> dict:
    base = {
        "id": GOLD["id"],
        "valid": True,
        "error_rules": [],
        "invoice": copy.deepcopy(GOLD["invoice"]),
    }
    base.update(overrides)
    return base


def expected_field_count() -> int:
    invoice = DOCUMENT.invoice
    return (
        HEADER_FIELDS
        + FIELDS_PER_LINE * len(invoice.lines)
        + FIELDS_PER_VAT_GROUP * len(invoice.vat_breakdown)
    )


def test_identical_invoice_has_no_wrong_fields():
    assert wrong_fields(DOCUMENT.invoice, DOCUMENT.invoice)[0] == []


def test_number_of_comparisons_matches_the_model():
    assert wrong_fields(DOCUMENT.invoice, DOCUMENT.invoice)[1] == expected_field_count()


def test_one_changed_field_is_one_wrong_field():
    changed = DOCUMENT.invoice.model_copy(update={"number": "other"})
    assert wrong_fields(DOCUMENT.invoice, changed)[0] == ["number"]


def test_changed_line_field_is_reported_by_position():
    line = DOCUMENT.invoice.lines[0].model_copy(update={"description": "other"})
    changed = DOCUMENT.invoice.model_copy(update={"lines": (line, *DOCUMENT.invoice.lines[1:])})
    assert wrong_fields(DOCUMENT.invoice, changed)[0] == ["lines.0.description"]


def test_missing_prediction_makes_every_field_wrong():
    wrong, compared = wrong_fields(DOCUMENT.invoice, None)
    assert len(wrong) == compared == expected_field_count()


def test_extra_predicted_line_counts_as_wrong_fields():
    lines = DOCUMENT.invoice.lines
    extra = DOCUMENT.invoice.model_copy(update={"lines": (*lines, lines[0])})
    wrong, compared = wrong_fields(DOCUMENT.invoice, extra)
    assert (len(wrong), compared) == (FIELDS_PER_LINE, expected_field_count() + FIELDS_PER_LINE)


def test_amounts_differing_only_in_trailing_zeros_are_equal():
    pred = prediction()
    pred["invoice"]["gross_total"] = GOLD["invoice"]["gross_total"] + "0"
    assert score([GOLD], [pred]).by_format["ubl"].field_accuracy == 1.0


def test_field_accuracy_is_correct_over_total():
    pred = prediction()
    pred["invoice"]["number"] = "other"
    bucket = score([GOLD], [pred]).by_format["ubl"]
    assert bucket.field_accuracy == pytest.approx(1 - 1 / expected_field_count())


def test_document_without_prediction_scores_zero_and_fails_the_gate():
    report = score([GOLD], [])
    assert (report.by_format["ubl"].field_accuracy, report.gate_passed) == (0.0, False)


def test_perfect_predictions_pass_the_gate():
    assert score([GOLD], [prediction()]).gate_passed


def test_one_wrong_field_fails_the_gate():
    pred = prediction()
    pred["invoice"]["currency"] = "USD"
    assert not score([GOLD], [pred]).gate_passed


def test_empty_evaluation_does_not_pass_the_gate():
    assert not score([], []).gate_passed


def test_wrong_verdict_fails_the_gate():
    assert not score([GOLD], [prediction(valid=False)]).gate_passed


def test_missing_verdict_is_not_correct():
    assert not validation_correct(GOLD, prediction(valid=None))


def test_expected_rejection_needs_the_expected_rule():
    gold = {**GOLD, "expected_valid": False, "expected_rule": "BR-CO-15"}
    assert not validation_correct(gold, prediction(valid=False, error_rules=["BR-DE-15"]))


def test_expected_rejection_with_its_rule_among_others_is_correct():
    gold = {**GOLD, "expected_valid": False, "expected_rule": "BR-CO-15"}
    assert validation_correct(gold, prediction(valid=False, error_rules=["BR-CO-16", "BR-CO-15"]))


def test_failures_name_the_document_and_the_wrong_fields():
    pred = prediction()
    pred["invoice"]["number"] = "other"
    (failure,) = score([GOLD], [pred]).failures
    assert (failure["id"], failure["wrong_fields"]) == (GOLD["id"], ["number"])
