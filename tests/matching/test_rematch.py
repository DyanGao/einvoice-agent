"""Matching again when new data arrives: a later invoice of the same
supplier, or a new transfer in the ERP's bank account."""

from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import db
from app.checks.service import Checker
from app.db.review import approve
from app.erp import ErpUnavailable, HttpErpClient
from app.ingest import ingest
from app.rematch import rematch_supplier, watch_bank
from eval.generators.canonical import XRECHNUNG
from eval.generators.world import PAID_COMBINED, PAID_ONCE, PO_EXACT, PO_MISSING, build_world
from eval.generators.xrechnung import render_ubl
from mock_erp import db as erp_db
from mock_erp import seed
from mock_erp.main import create_app as create_erp
from tests.support import inline_queue


@pytest.fixture(scope="module")
def world():
    return build_world()


class Erp:
    """The mock ERP in memory, holding the world without some transfers,
    which a test can then let arrive."""

    def __init__(self, world, withheld: set[str]) -> None:
        self.engine = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        self._withheld = [tx for tx in world.bank_transactions if tx.id in withheld]
        kept = tuple(tx for tx in world.bank_transactions if tx.id not in withheld)
        self._now = max(tx.booking_date for tx in world.bank_transactions) + timedelta(days=1)
        seed.load(replace(world, bank_transactions=kept), self.engine)
        self.client = HttpErpClient(TestClient(create_erp(self.engine)))
        self.down = False

    def arrive(self) -> None:
        """The withheld transfers are booked now: after everything else."""
        with Session(self.engine) as session, session.begin():
            for tx in self._withheld:
                session.add(
                    erp_db.BankTransactionRow(
                        id=tx.id,
                        booking_date=self._now,
                        amount_cents=erp_db.cents(tx.amount),
                        counterparty_name=tx.counterparty_name,
                        counterparty_iban=tx.counterparty_iban,
                        purpose=tx.purpose,
                    )
                )

    def __getattr__(self, name):
        if self.down:
            raise ErpUnavailable("connection refused")
        return getattr(self.client, name)


def paid_once(world, po_kind=PO_EXACT):
    return next(
        entry
        for entry in world.invoices
        if entry.payment_kind == PAID_ONCE
        and entry.payment_ref_kind == "exact"
        and entry.po_ref_kind == po_kind
    )


def process(session_factory, entry, checker) -> str:
    queue = inline_queue(session_factory, checker=checker)
    xml = render_ubl(entry.invoice, XRECHNUNG)
    return ingest(session_factory, queue, xml, f"{entry.id}.xml").document_id


def payments(session_factory, document_id) -> list[str]:
    with session_factory() as session:
        match = session.get(db.DocumentRow, document_id).match
        return [] if match is None else [p["bank_transaction_id"] for p in match.payments]


def watch(session_factory, erp, checker) -> list[str]:
    with session_factory.begin() as session:
        return watch_bank(session, erp, checker, db.DEFAULT_TENANT_ID)


def test_transfer_arriving_after_the_invoice_is_matched_when_it_arrives(session_factory, world):
    entry = paid_once(world)
    (transfer,) = entry.payments
    erp = Erp(world, {transfer.bank_transaction_id})
    checker = Checker(erp)
    document_id = process(session_factory, entry, checker)
    watch(session_factory, erp, checker)  # everything already in the ERP: nothing to do
    assert payments(session_factory, document_id) == []

    erp.arrive()
    assert watch(session_factory, erp, checker) == [transfer.bank_transaction_id]
    assert payments(session_factory, document_id) == [transfer.bank_transaction_id]


def test_rematch_is_recorded_with_what_triggered_it(session_factory, world):
    entry = paid_once(world)
    erp = Erp(world, {entry.payments[0].bank_transaction_id})
    checker = Checker(erp)
    document_id = process(session_factory, entry, checker)
    watch(session_factory, erp, checker)
    erp.arrive()
    watch(session_factory, erp, checker)
    with session_factory() as session:
        event = session.get(db.DocumentRow, document_id).audit_events[-1]
    assert (event.event, event.detail["trigger"]) == (
        "checked",
        f"new bank transaction {entry.payments[0].bank_transaction_id}",
    )


def test_watching_again_without_new_transfers_does_nothing(session_factory, world):
    erp = Erp(world, set())
    checker = Checker(erp)
    process(session_factory, paid_once(world), checker)
    first = watch(session_factory, erp, checker)
    with session_factory() as session:
        events = session.query(db.AuditEventRow).count()
    assert (bool(first), watch(session_factory, erp, checker)) == (True, [])
    with session_factory() as session:
        assert session.query(db.AuditEventRow).count() == events


def test_combined_transfer_is_matched_to_all_invoices_once_the_last_one_arrives(
    session_factory, world
):
    """One transfer pays three invoices; it is only recognised with all three
    on file, so the first two are matched again when the third comes."""
    combined = [entry for entry in world.invoices if entry.payment_kind == PAID_COMBINED]
    transfer = combined[0].payments[0].bank_transaction_id
    group = [entry for entry in combined if entry.payments[0].bank_transaction_id == transfer]
    checker = Checker(Erp(world, set()))
    ids = [process(session_factory, entry, checker) for entry in group]
    assert [payments(session_factory, document_id) for document_id in ids] == [[transfer]] * 3


def test_approved_invoice_gets_its_payment_but_keeps_what_the_reviewer_saw(session_factory, world):
    entry = paid_once(world, PO_MISSING)  # order found by content: goes to review
    erp = Erp(world, {entry.payments[0].bank_transaction_id})
    checker = Checker(erp)
    document_id = process(session_factory, entry, checker)
    watch(session_factory, erp, checker)
    with session_factory.begin() as session:
        document = session.get(db.DocumentRow, document_id)
        approve(session, document, "anna")
    with session_factory() as session:
        before = session.get(db.DocumentRow, document_id)
        kept = (
            [f.type for f in before.findings],
            before.decision.reasons,
            before.match.po_number,
            before.match.delivery_id,
        )

    erp.arrive()
    watch(session_factory, erp, checker)
    with session_factory() as session:
        after = session.get(db.DocumentRow, document_id)
        assert (
            after.review_state,
            [p["bank_transaction_id"] for p in after.match.payments],
            after.audit_events[-1].event,
        ) == ("approved", [entry.payments[0].bank_transaction_id], "payments_matched")
        assert (
            [f.type for f in after.findings],
            after.decision.reasons,
            after.match.po_number,
            after.match.delivery_id,
        ) == kept


def test_erp_failing_during_a_rematch_leaves_the_invoice_as_it_was(session_factory, world):
    entry = paid_once(world)
    erp = Erp(world, {entry.payments[0].bank_transaction_id})
    checker = Checker(erp)
    document_id = process(session_factory, entry, checker)
    with session_factory() as session:
        document = session.get(db.DocumentRow, document_id)
        before = (document.match.po_number, document.review_state, len(document.audit_events))

    erp.arrive()
    erp.down = True
    with session_factory.begin() as session, pytest.raises(ErpUnavailable):
        rematch_supplier(session, checker, db.DEFAULT_TENANT_ID, entry.supplier_id, trigger="test")
    with session_factory() as session:
        document = session.get(db.DocumentRow, document_id)
        after = (document.match.po_number, document.review_state, len(document.audit_events))
    assert after == before


def test_new_invoice_is_processed_even_if_the_erp_fails_while_matching_others_again(
    session_factory, world
):
    """Half-way through, the ERP stops answering: the new invoice is not lost."""
    entry = paid_once(world)
    erp = Erp(world, {entry.payments[0].bank_transaction_id})
    calls = {"left": None}
    real = erp.client

    class Flaky:
        def __getattr__(self, name):
            def call(*args):
                if calls["left"] is not None:
                    if calls["left"] == 0:
                        raise ErpUnavailable("connection reset")
                    calls["left"] -= 1
                return getattr(real, name)(*args)

            return call

    checker = Checker(Flaky())
    process(session_factory, entry, checker)
    later = next(
        other
        for other in world.invoices
        if other.supplier_id == entry.supplier_id and other is not entry
    )
    calls["left"] = 3  # enough for the new invoice itself, not for matching the first again
    document_id = process(session_factory, later, checker)
    with session_factory() as session:
        assert session.get(db.DocumentRow, document_id).status == "valid"


def test_bank_watch_fails_loudly_when_the_erp_does_not_answer_and_retries_later(
    session_factory, world
):
    entry = paid_once(world)
    erp = Erp(world, {entry.payments[0].bank_transaction_id})
    checker = Checker(erp)
    document_id = process(session_factory, entry, checker)
    erp.arrive()
    erp.down = True
    with pytest.raises(ErpUnavailable):
        watch(session_factory, erp, checker)
    erp.down = False
    assert entry.payments[0].bank_transaction_id in watch(session_factory, erp, checker)
    assert payments(session_factory, document_id) == [entry.payments[0].bank_transaction_id]


def test_fully_paid_invoice_is_not_matched_again(session_factory, world):
    entry = paid_once(world)
    erp = Erp(world, set())
    checker = Checker(erp)
    document_id = process(session_factory, entry, checker)
    assert payments(session_factory, document_id) == [entry.payments[0].bank_transaction_id]
    watch(session_factory, erp, checker)
    with session_factory() as session:
        events = [e.event for e in session.get(db.DocumentRow, document_id).audit_events]
    assert events.count("checked") == 1


def test_provisional_payment_is_dropped_when_an_invoice_of_the_same_total_arrives(
    session_factory, world
):
    """A transfer without an invoice number, of an amount two invoices share,
    cannot be told apart: once the twin is on file, neither claims it."""
    entry = paid_once(world, PO_MISSING)
    twin = next(
        other
        for other in world.invoices
        if other.supplier_id == entry.supplier_id
        and other is not entry
        and other.invoice.gross_total == entry.invoice.gross_total
    )
    checker = Checker(Erp(world, {entry.payments[0].bank_transaction_id}))
    first = process(session_factory, entry, checker)
    guessed = payments(session_factory, first)
    process(session_factory, twin, checker)
    assert (guessed, payments(session_factory, first)) == (
        [twin.payments[0].bank_transaction_id],
        [],
    )


def test_matching_a_supplier_again_leaves_a_rejected_invoice_alone(session_factory, world):
    entry = paid_once(world, PO_MISSING)
    erp = Erp(world, set())
    checker = Checker(erp)
    document_id = process(session_factory, entry, checker)
    with session_factory.begin() as session:
        document = session.get(db.DocumentRow, document_id)
        supplier_id = document.match.supplier_id
        db.reject(session, document, "anna", "duplicate")
        events = len(document.audit_events)
    with session_factory.begin() as session:
        touched = rematch_supplier(
            session, checker, db.DEFAULT_TENANT_ID, supplier_id, trigger="test"
        )
    with session_factory() as session:
        document = session.get(db.DocumentRow, document_id)
        assert (document.review_state, len(document.audit_events)) == ("rejected", events)
    assert document_id not in touched
