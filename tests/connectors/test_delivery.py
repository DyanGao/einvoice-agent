"""Delivering decided invoices to the ERP, exactly once."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app import db
from app.api.main import create_app
from app.checks.service import Checker
from app.connectors import CsvExportConnector, DeliveryFailed, ErpApiConnector
from app.db.review import approve
from app.delivery import deliver_due
from app.erp import HttpErpClient
from app.ingest import ingest
from eval.generators.canonical import XRECHNUNG
from eval.generators.world import PAID_ONCE, PO_EXACT, PO_MISSING, build_world
from eval.generators.xrechnung import render_ubl
from mock_erp import seed
from mock_erp.main import create_app as create_erp
from tests.support import inline_queue, settings_for

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture
def erp_http(world):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    seed.load(world, engine)
    return TestClient(create_erp(engine))


@pytest.fixture
def checker(erp_http):
    return Checker(HttpErpClient(erp_http))


@pytest.fixture
def connector(erp_http):
    return ErpApiConnector(erp_http)


def entry_of(world, po_kind):
    return next(
        entry
        for entry in world.invoices
        if entry.po_ref_kind == po_kind and entry.payment_kind == PAID_ONCE
    )


def process(session_factory, entry, checker) -> str:
    queue = inline_queue(session_factory, checker=checker)
    xml = render_ubl(entry.invoice, XRECHNUNG)
    return ingest(session_factory, queue, xml, f"{entry.id}.xml").document_id


def bookings(erp_http) -> list[dict]:
    return erp_http.get("/booked-invoices").json()["booked_invoices"]


def deliveries(session_factory, document_id) -> list[tuple]:
    with session_factory() as session:
        return [
            (d.connector, d.status, d.attempts, d.external_ref)
            for d in session.get(db.DocumentRow, document_id).deliveries
        ]


def test_invoice_decided_auto_is_booked_in_the_erp(
    session_factory, world, checker, connector, erp_http
):
    entry = entry_of(world, PO_EXACT)
    document_id = process(session_factory, entry, checker)
    deliver_due(session_factory, [connector], NOW)
    (booked,) = bookings(erp_http)
    assert (booked["number"], booked["gross_total"], booked["po_number"], booked["reference"]) == (
        entry.invoice.number,
        str(entry.invoice.gross_total),
        entry.po_number,
        document_id,
    )
    assert deliveries(session_factory, document_id) == [
        ("erp_api", "delivered", 1, str(booked["id"]))
    ]


def test_delivering_again_books_nothing_twice(session_factory, world, checker, connector, erp_http):
    process(session_factory, entry_of(world, PO_EXACT), checker)
    deliver_due(session_factory, [connector], NOW)
    deliver_due(session_factory, [connector], NOW + timedelta(hours=1))
    assert len(bookings(erp_http)) == 1


def test_invoice_waiting_for_review_is_not_delivered(
    session_factory, world, checker, connector, erp_http
):
    document_id = process(session_factory, entry_of(world, PO_MISSING), checker)  # order by content
    deliver_due(session_factory, [connector], NOW)
    assert (bookings(erp_http), deliveries(session_factory, document_id)) == ([], [])


def test_approved_invoice_is_delivered(session_factory, world, checker, connector, erp_http):
    document_id = process(session_factory, entry_of(world, PO_MISSING), checker)
    with session_factory.begin() as session:
        approve(session, session.get(db.DocumentRow, document_id), "anna")
    deliver_due(session_factory, [connector], NOW)
    assert [booked["reference"] for booked in bookings(erp_http)] == [document_id]


def test_no_delivery_without_a_recorded_decision(session_factory, world, connector, erp_http):
    """Processed without checks, a valid e-invoice skips review but has no
    decision: it is not delivered."""
    document_id = process(session_factory, entry_of(world, PO_EXACT), checker=None)
    with session_factory() as session:
        assert session.get(db.DocumentRow, document_id).review_state == "not_required"
    deliver_due(session_factory, [connector], NOW)
    assert bookings(erp_http) == []


class CrashAfterSending:
    """Books in the ERP, then dies before the application records it."""

    name = "erp_api"

    def __init__(self, inner):
        self.inner = inner

    def deliver(self, booking, key):
        self.inner.deliver(booking, key)
        raise RuntimeError("process killed")


def test_crash_between_booking_and_recording_does_not_book_twice(
    session_factory, world, checker, connector, erp_http
):
    document_id = process(session_factory, entry_of(world, PO_EXACT), checker)
    with pytest.raises(RuntimeError):
        deliver_due(session_factory, [CrashAfterSending(connector)], NOW)
    assert deliveries(session_factory, document_id)[0][1] == "pending"
    deliver_due(session_factory, [connector], NOW + timedelta(hours=1))
    (booked,) = bookings(erp_http)
    assert deliveries(session_factory, document_id) == [
        ("erp_api", "delivered", 2, str(booked["id"]))
    ]


class Down:
    name = "erp_api"

    def deliver(self, booking, key):
        raise DeliveryFailed("ERP not reachable", retryable=True)


def test_erp_down_is_retried_later_not_at_once(
    session_factory, world, checker, connector, erp_http
):
    document_id = process(session_factory, entry_of(world, PO_EXACT), checker)
    deliver_due(session_factory, [Down()], NOW)
    deliver_due(session_factory, [connector], NOW + timedelta(seconds=1))  # too early
    assert bookings(erp_http) == []
    deliver_due(session_factory, [connector], NOW + timedelta(hours=1))
    assert deliveries(session_factory, document_id)[0][1] == "delivered"


def test_delivery_gives_up_after_the_last_attempt_and_says_why(session_factory, world, checker):
    document_id = process(session_factory, entry_of(world, PO_EXACT), checker)
    when = NOW
    for _ in range(20):
        deliver_due(session_factory, [Down()], when)
        when += timedelta(days=1)
    with session_factory() as session:
        (delivery,) = session.get(db.DocumentRow, document_id).deliveries
        assert (delivery.status, delivery.last_error) == ("failed", "ERP not reachable")


def test_refused_booking_is_not_retried_and_is_surfaced(session_factory, world, checker, erp_http):
    class Refused:
        name = "erp_api"
        calls = 0

        def deliver(self, booking, key):
            Refused.calls += 1
            raise DeliveryFailed("ERP refused: no supplier S999", retryable=False)

    document_id = process(session_factory, entry_of(world, PO_EXACT), checker)
    deliver_due(session_factory, [Refused()], NOW)
    deliver_due(session_factory, [Refused()], NOW + timedelta(days=1))
    with session_factory() as session:
        document = session.get(db.DocumentRow, document_id)
        events = [event.event for event in document.audit_events]
        assert (document.deliveries[0].status, Refused.calls, events[-1]) == (
            "failed",
            1,
            "delivery_failed",
        )


def test_every_attempt_is_in_the_audit_trail(session_factory, world, checker, connector):
    document_id = process(session_factory, entry_of(world, PO_EXACT), checker)
    deliver_due(session_factory, [Down()], NOW)
    deliver_due(session_factory, [connector], NOW + timedelta(hours=1))
    with session_factory() as session:
        events = [
            (event.event, event.detail["connector"])
            for event in session.get(db.DocumentRow, document_id).audit_events
            if event.event.startswith("deliver")
        ]
    assert events == [("delivery_failed", "erp_api"), ("delivered", "erp_api")]


def test_connector_turns_server_errors_into_retries_and_refusals_into_failures():
    def answer(status):
        return ErpApiConnector(
            httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(status, json={})),
                base_url="http://erp",
            )
        )

    from app.connectors.base import Booking

    booking = Booking(
        supplier_id="S001",
        number="RE-1",
        issue_date="2026-03-01",
        due_date=None,
        currency="EUR",
        net_total="100.00",
        vat_total="19.00",
        gross_total="119.00",
        amount_due="119.00",
        po_number=None,
        iban=None,
        reference="doc-1",
    )
    outcomes = []
    for status in (503, 422):
        with pytest.raises(DeliveryFailed) as failed:
            answer(status).deliver(booking, "k")
        outcomes.append(failed.value.retryable)
    assert outcomes == [True, False]


def test_document_detail_says_where_and_as_what_it_was_booked(
    session_factory, tmp_path, world, checker, connector, erp_http
):
    queue = inline_queue(session_factory, checker=checker)
    client = TestClient(create_app(session_factory, queue, settings_for(tmp_path), checker))
    document_id = process(session_factory, entry_of(world, PO_EXACT), checker)
    deliver_due(session_factory, [connector], NOW)
    (delivery,) = client.get(f"/documents/{document_id}").json()["deliveries"]
    (booked,) = bookings(erp_http)
    assert (delivery["connector"], delivery["status"], delivery["external_ref"]) == (
        "erp_api",
        "delivered",
        str(booked["id"]),
    )


def test_invoice_goes_to_every_connector_once(
    session_factory, tmp_path, world, checker, connector, erp_http
):
    document_id = process(session_factory, entry_of(world, PO_EXACT), checker)
    export = CsvExportConnector(tmp_path)
    for _ in range(2):
        deliver_due(session_factory, [connector, export], NOW)
    (listed,) = tmp_path.iterdir()
    rows = listed.read_text(encoding="cp1252").splitlines()
    assert (len(bookings(erp_http)), len(rows), rows[1].endswith(document_id)) == (1, 2, True)
    assert [d[:2] for d in deliveries(session_factory, document_id)] == [
        ("csv_export", "delivered"),
        ("erp_api", "delivered"),
    ]


def test_approved_credit_note_is_not_booked_as_an_invoice(
    session_factory, world, checker, connector, erp_http
):
    entry = entry_of(world, PO_EXACT)
    credit_note = replace(entry, invoice=replace(entry.invoice, type_code="381"))
    document_id = process(session_factory, credit_note, checker)
    with session_factory.begin() as session:
        approve(session, session.get(db.DocumentRow, document_id), "anna")
    deliver_due(session_factory, [connector], NOW)
    with session_factory() as session:
        (delivery,) = session.get(db.DocumentRow, document_id).deliveries
        assert (bookings(erp_http), delivery.status, "credit notes" in delivery.last_error) == (
            [],
            "failed",
            True,
        )
