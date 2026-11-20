"""Selecting the copies of an invoice that can be rejected together, and
rejecting them."""

from decimal import Decimal

import pytest

from app import db
from app.checks.service import Checker
from app.db.duplicates import redundant_copies, reject_copies
from app.ingest import ingest
from eval.generators.canonical import XRECHNUNG
from eval.generators.world import PAID_ONCE, PO_EXACT, build_world
from eval.generators.xrechnung import render_cii, render_ubl
from eval.match import erp_for
from tests.support import inline_queue


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture
def entry(world):
    return next(
        e for e in world.invoices if e.po_ref_kind == PO_EXACT and e.payment_kind == PAID_ONCE
    )


@pytest.fixture
def checker(world):
    return Checker(erp_for(world))


def send(session_factory, checker, data, name="rechnung") -> str:
    queue = inline_queue(session_factory, checker=checker)
    return ingest(session_factory, queue, data, name).document_id


def ids(session_factory) -> list[str]:
    with session_factory() as session:
        return [copy.document.id for copy in redundant_copies(session, db.DEFAULT_TENANT_ID)]


def state(session_factory, document_id) -> str:
    with session_factory() as session:
        return session.get(db.DocumentRow, document_id).review_state


def test_invoice_sent_again_in_another_file_is_a_redundant_copy(session_factory, checker, entry):
    original = send(session_factory, checker, render_ubl(entry.invoice, XRECHNUNG))
    copy = send(session_factory, checker, render_cii(entry.invoice, XRECHNUNG))
    with session_factory() as session:
        (found,) = redundant_copies(session, db.DEFAULT_TENANT_ID)
        assert (found.document.id, found.original.id) == (copy, original)


def test_the_earliest_copy_is_kept_even_when_checked_again(session_factory, checker, entry):
    first = send(session_factory, checker, render_ubl(entry.invoice, XRECHNUNG))
    second = send(session_factory, checker, render_cii(entry.invoice, XRECHNUNG))
    with session_factory.begin() as session:
        # Checked again, the first now points at the second as well.
        document = session.get(db.DocumentRow, first)
        document.review_state = "pending"
        checker.check(session, document)
    assert ids(session_factory) == [second]


def test_copy_of_a_rejected_invoice_is_left_for_a_person(session_factory, checker, entry):
    original = send(session_factory, checker, render_ubl(entry.invoice, XRECHNUNG))
    send(session_factory, checker, render_cii(entry.invoice, XRECHNUNG))
    with session_factory.begin() as session:
        document = session.get(db.DocumentRow, original)
        document.review_state = "pending"
        db.reject(session, document, "anna", "not_addressed_to_us")
    assert ids(session_factory) == []


def test_copy_with_another_total_is_left_for_a_person(session_factory, checker, entry):
    send(session_factory, checker, render_ubl(entry.invoice, XRECHNUNG))
    copy = send(session_factory, checker, render_cii(entry.invoice, XRECHNUNG))
    with session_factory.begin() as session:
        draft = session.get(db.DocumentRow, copy).draft
        total = Decimal(draft.data["gross_total"]) + 1
        draft.data = {**draft.data, "gross_total": str(total)}
    assert ids(session_factory) == []


def test_copy_with_another_reason_for_review_is_left_for_a_person(session_factory, checker, entry):
    send(session_factory, checker, render_ubl(entry.invoice, XRECHNUNG))
    copy = send(session_factory, checker, render_cii(entry.invoice, XRECHNUNG))
    with session_factory.begin() as session:
        decision = session.get(db.DocumentRow, copy).decision
        decision.reasons = [*decision.reasons, {"cause": "low_confidence", "fields": ["number"]}]
    assert ids(session_factory) == []


def test_bulk_reject_rejects_the_copies_with_a_note_naming_the_original(
    session_factory, checker, entry
):
    original = send(session_factory, checker, render_ubl(entry.invoice, XRECHNUNG))
    copy = send(session_factory, checker, render_cii(entry.invoice, XRECHNUNG))
    with session_factory.begin() as session:
        result = reject_copies(session, db.DEFAULT_TENANT_ID, [copy, original, "missing"], "anna")
    assert (result.rejected, result.skipped) == ([copy], [original, "missing"])
    with session_factory() as session:
        event = session.get(db.DocumentRow, copy).audit_events[-1]
    assert (event.event, event.actor, event.detail["reason"]) == ("rejected", "anna", "duplicate")
    assert original in event.detail["note"]
    assert state(session_factory, original) != "rejected"
