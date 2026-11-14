from dataclasses import replace

import pytest

from app.checks.finding import Finding, FindingType, Severity
from eval.exceptions import Outcome, run, score
from eval.generators.exceptions import (
    CLEAN,
    DUPLICATE,
    IBAN,
    MISSING_PO,
    PRICE,
    build_exception_set,
)
from eval.generators.world import build_world


@pytest.fixture(scope="module")
def exceptions():
    return build_exception_set(build_world())


@pytest.fixture(scope="module")
def outcomes(exceptions):
    return run(exceptions)


def case_of(exceptions, kind):
    return next(case for case in exceptions.cases if case.kind == kind)


def finding(kind: FindingType, severity: Severity = Severity.REVIEW) -> Finding:
    return Finding(type=kind, severity=severity, message="x")


def test_finding_of_the_planted_kind_is_a_detection(exceptions):
    outcome = Outcome(case_of(exceptions, IBAN), (finding(FindingType.IBAN_CHANGED),))
    assert (outcome.detected(), outcome.flagged()) == (True, True)


def test_another_finding_sends_to_review_but_is_not_a_detection(exceptions):
    outcome = Outcome(case_of(exceptions, PRICE), (finding(FindingType.PO_NOT_FOUND),))
    assert (outcome.detected(), outcome.flagged()) == (False, True)


def test_finding_for_information_only_counts_for_nothing(exceptions):
    outcome = Outcome(
        case_of(exceptions, MISSING_PO), (finding(FindingType.MISSING_PO, Severity.INFO),)
    )
    assert (outcome.detected(), outcome.flagged()) == (False, False)


def test_recall_is_the_share_of_planted_cases_detected(exceptions):
    case = case_of(exceptions, IBAN)
    result = score([Outcome(case, (finding(FindingType.IBAN_CHANGED),)), Outcome(case, ())])
    assert result["by_kind"][IBAN]["recall"] == 0.5


def test_alarm_on_a_case_of_another_kind_lowers_precision(exceptions):
    alarm = finding(FindingType.IBAN_CHANGED)
    result = score(
        [
            Outcome(case_of(exceptions, IBAN), (alarm,)),
            Outcome(case_of(exceptions, CLEAN), (alarm,)),
        ]
    )
    assert result["by_kind"][IBAN]["precision"] == 0.5


def test_kind_nobody_raised_has_no_precision(exceptions):
    result = score([Outcome(case_of(exceptions, IBAN), ())])
    assert result["by_kind"][IBAN]["precision"] is None


def test_clean_invoice_sent_to_review_is_counted_with_its_reason(exceptions):
    clean = case_of(exceptions, CLEAN)
    result = score([Outcome(clean, (finding(FindingType.PO_NOT_FOUND),)), Outcome(clean, ())])
    assert (result["clean"]["flagged"], result["clean"]["by_finding"]) == (
        1,
        {"po_not_found": 1},
    )


def test_missed_cases_are_listed(exceptions):
    case = replace(case_of(exceptions, DUPLICATE), id="exc-x")
    assert [miss["id"] for miss in score([Outcome(case, ())])["missed"]] == ["exc-x"]


def test_every_case_is_checked_once(exceptions, outcomes):
    assert [outcome.case.id for outcome in outcomes] == [case.id for case in exceptions.cases]


def test_a_case_never_meets_another_case_as_an_invoice_on_file(exceptions, outcomes):
    backwards = run(replace(exceptions, cases=exceptions.cases[::-1]))
    assert {o.case.id: [f.type for f in o.findings] for o in backwards} == {
        o.case.id: [f.type for f in o.findings] for o in outcomes
    }


@pytest.mark.parametrize("kind", [IBAN, DUPLICATE])
def test_changed_accounts_and_duplicates_are_caught_on_the_held_out_half(outcomes, kind):
    held_out = [outcome for outcome in outcomes if outcome.case.split == "test"]
    assert score(held_out)["by_kind"][kind]["recall"] >= 0.95


def test_no_invoice_with_a_planted_problem_is_booked_without_review(outcomes):
    assert [o.case.id for o in outcomes if o.case.kind != CLEAN and o.route == "auto"] == []


def test_clean_invoices_held_back_are_counted_by_reason(exceptions):
    clean = case_of(exceptions, CLEAN)
    result = score(
        [
            Outcome(clean, (), route="review", causes=("order_by_content",)),
            Outcome(clean, (), route="auto"),
        ]
    )
    assert (result["clean"]["booked_without_review"], result["clean"]["held_back"]) == (
        1,
        {"order_by_content": 1},
    )
