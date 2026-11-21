"""The credit-note eval (eval/credits.py): what counts as a right link,
payment and booking, measured on a small set through the application."""

from dataclasses import replace

import pytest

from eval.credits import Outcome, file_reader, read_correctly, run, run_readers, score
from eval.generators.credits import (
    CII,
    INVOICE_NOT_ON_FILE,
    OFFSET,
    UBL,
    ExpectedPayment,
    build_credit_set,
)
from eval.generators.world import build_world


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def credit_set(world):
    # Enough for every variant, two of each, and the settlements dealt with them.
    return build_credit_set(world, count=40)


@pytest.fixture(scope="module")
def numbers(world):
    return {entry.id: entry.invoice.number for entry in world.invoices}


@pytest.fixture(scope="module")
def runs(credit_set):
    return run_readers(credit_set, {"correctly": read_correctly, "as_read": file_reader()})


@pytest.fixture(scope="module")
def correctly(runs):
    return runs["correctly"]


@pytest.fixture(scope="module")
def as_read(runs):
    return runs["as_read"]


def test_one_reader_runs_alone_as_in_a_run_of_several(credit_set, correctly):
    few = replace(credit_set, cases=credit_set.cases[:3])
    alone = run(few, read_correctly)
    assert [o.linked_to for o in alone] == [o.linked_to for o in correctly[:3]]


def test_read_correctly_every_credit_note_is_linked_right(correctly, numbers):
    result = score(correctly, numbers)
    assert result["link"]["right"] == result["cases"]
    assert result["wrong_links"] == []


def test_read_correctly_every_refund_and_offset_is_found(correctly, numbers):
    result = score(correctly, numbers)
    assert (result["payments"]["right"], result["payments"]["wrong"]) == (result["cases"], 0)
    offsets = result["offset_invoices"]
    assert offsets["cases"] > 0
    assert offsets["right"] == offsets["cases"]


def test_a_linked_credit_note_is_booked_as_a_credit_against_its_invoice(correctly, numbers):
    for outcome in correctly:
        assert outcome.booked_type == "381"
        if outcome.linked_to is not None:
            assert outcome.booked_invoice == numbers[outcome.linked_to]
    assert score(correctly, numbers)["booking"]["booked_without_review"] == 0


def test_a_credit_note_for_an_invoice_never_received_is_not_linked(correctly):
    unlinked = [o for o in correctly if o.case.variant == INVOICE_NOT_ON_FILE]
    assert unlinked
    assert all(o.linked_to is None and o.link_right() for o in unlinked)


def test_xml_credit_notes_are_read_without_an_error(as_read, numbers):
    reading = score(as_read, numbers)["reading"]
    for fmt in (UBL, CII):
        assert reading[fmt]["field_accuracy"] == 1.0
        assert reading[fmt]["read_as_credit_note"] == reading[fmt]["cases"]


def test_credit_notes_read_from_the_files_are_never_booked_as_invoices(as_read, numbers):
    booking = score(as_read, numbers)["booking"]
    assert (booking["booked_as_invoice"], booking["booked_without_review"]) == (0, 0)


def _outcome(case, **changes) -> Outcome:
    base = Outcome(
        case=case,
        draft=None,
        wrong_fields=(),
        compared=1,
        linked_to=case.expected_invoice_id,
        payments=frozenset(case.payments),
        offset_payments=frozenset(case.offset_payments),
        booked_type="381",
        booked_invoice=None,
        route="review",
    )
    return replace(base, **changes)


def test_a_link_to_another_invoice_is_wrong_and_no_link_only_missed(credit_set):
    case = next(c for c in credit_set.cases if c.expected_invoice_id is not None)
    wrong = _outcome(case, linked_to="inv-9999")
    missed = _outcome(case, linked_to=None)
    assert (wrong.link_right(), wrong.link_wrong()) == (False, True)
    assert (missed.link_right(), missed.link_wrong()) == (False, False)


def test_a_transfer_named_that_did_not_settle_it_is_a_wrong_payment(credit_set):
    case = next(c for c in credit_set.cases if c.settlement == OFFSET)
    other = frozenset({ExpectedPayment("TX-000001", case.credit.gross_total)})
    assert _outcome(case, payments=other).payments_wrong()
    assert not _outcome(case, payments=frozenset()).payments_wrong()


def test_a_booking_of_another_type_is_a_credit_booked_as_an_invoice(credit_set, numbers):
    case = credit_set.cases[0]
    outcomes = [_outcome(case, booked_type="380"), _outcome(case, booked_type=None)]
    assert score(outcomes, numbers)["booking"]["booked_as_invoice"] == 1
