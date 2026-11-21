"""Booking through the ERP's HTML screens with a browser (the last resort)."""

import json
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from urllib.parse import urlsplit

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.connectors import Booking, DeliveryFailed, ErpApiConnector, ErpFormConnector
from eval.generators.world import build_world
from mock_erp import db as erp_db
from mock_erp import seed
from mock_erp.main import create_app as create_erp

BOOKING = Booking(
    supplier_id="S001",
    number="RE-2026-00042",
    issue_date="2026-03-01",
    due_date="2026-03-31",
    currency="EUR",
    net_total="1234.50",
    vat_total="234.56",
    gross_total="1469.06",
    amount_due="1469.06",
    po_number="PO-123456",
    iban="DE89370400440532013000",
    reference="doc-1",
)
NOT_COMPARED = {"id", "channel", "booked_at"}


@pytest.fixture(scope="module")
def world():
    return build_world()


def erp_engine(world):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    seed.load(world, engine)
    return engine


@contextmanager
def serving(app):
    """The app on a free localhost port, in a thread, for a real browser."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@contextmanager
def erp(world, quirk=None):
    engine = erp_engine(world)
    with serving(create_erp(engine, ui_quirk=quirk)) as url:
        yield url, engine


def form_connector(url, tmp_path, hosts=None):
    """Allowed: the ERP's own host and port, unless the test says otherwise."""
    allowed = set(hosts) if hosts is not None else {urlsplit(url).netloc}
    return ErpFormConnector(url, allowed_hosts=allowed, log_dir=tmp_path / "browser")


def stored(engine) -> list[dict]:
    from mock_erp.bookings import booked_json

    with Session(engine) as session:
        return [booked_json(row) for row in session.scalars(select(erp_db.BookedInvoiceRow))]


def actions(tmp_path) -> list[dict]:
    (log,) = (tmp_path / "browser").glob("*/actions.jsonl")
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_invoice_is_booked_through_the_form_and_read_back(world, tmp_path):
    with erp(world) as (url, engine):
        ref = form_connector(url, tmp_path).deliver(BOOKING, "k")
    (booked,) = stored(engine)
    assert (ref, booked["channel"]) == (str(booked["id"]), "form")
    assert {k: booked[k] for k in BOOKING.as_json()} == BOOKING.as_json()


def test_form_and_api_produce_the_identical_record(world, tmp_path):
    """The plan's success criterion: with the API off, the UI path books the same."""
    with erp(world) as (url, engine):
        form_connector(url, tmp_path).deliver(BOOKING, "k")
    api_engine = erp_engine(world)
    ErpApiConnector(TestClient(create_erp(api_engine))).deliver(BOOKING, "k")
    (by_form,), (by_api,) = stored(engine), stored(api_engine)
    assert {k: v for k, v in by_form.items() if k not in NOT_COMPARED} == {
        k: v for k, v in by_api.items() if k not in NOT_COMPARED
    }


def test_delivering_again_finds_the_booking_instead_of_booking_twice(world, tmp_path):
    """No idempotency key on a screen: the connector searches first. That is
    also what a retry after a crash between booking and recording relies on."""
    with erp(world) as (url, engine):
        connector = form_connector(url, tmp_path)
        first = connector.deliver(BOOKING, "k")
        again = connector.deliver(BOOKING, "k")
    assert (again, len(stored(engine))) == (first, 1)


def test_existing_booking_with_other_values_is_refused(world, tmp_path):
    with erp(world) as (url, engine):
        form_connector(url, tmp_path).deliver(BOOKING, "k")
        with pytest.raises(DeliveryFailed, match="other values") as refused:
            form_connector(url, tmp_path).deliver(replace(BOOKING, gross_total="1470.00"), "k")
    assert (refused.value.retryable, len(stored(engine))) == (False, 1)


def test_renamed_field_fails_cleanly_with_a_screenshot_and_books_nothing(world, tmp_path):
    with (
        erp(world, quirk="renamed") as (url, engine),
        pytest.raises(DeliveryFailed, match="Nettobetrag") as failed,
    ):
        form_connector(url, tmp_path).deliver(BOOKING, "k")
    assert (failed.value.retryable, stored(engine)) == (False, [])
    assert list((tmp_path / "browser").glob("*/failure-*.png"))


def test_fields_in_another_order_are_still_filled_right(world, tmp_path):
    with erp(world, quirk="reordered") as (url, engine):
        form_connector(url, tmp_path).deliver(BOOKING, "k")
    (booked,) = stored(engine)
    assert {k: booked[k] for k in BOOKING.as_json()} == BOOKING.as_json()


def test_value_the_erp_stored_differently_is_caught_by_the_read_back(world, tmp_path):
    with (
        erp(world, quirk="truncating") as (url, engine),
        pytest.raises(DeliveryFailed, match="Rechnungsnummer") as failed,
    ):
        form_connector(url, tmp_path).deliver(BOOKING, "k")
    assert failed.value.retryable is False
    assert list((tmp_path / "browser").glob("*/failure-*.png"))


def test_form_posting_to_a_host_not_on_the_allowlist_is_blocked(world, tmp_path):
    with (
        erp(world, quirk="foreign_action") as (url, engine),
        pytest.raises(DeliveryFailed, match="elsewhere.example") as failed,
    ):
        form_connector(url, tmp_path).deliver(BOOKING, "k")
    assert (failed.value.retryable, stored(engine)) == (False, [])
    assert any(
        entry["action"] == "blocked" and "elsewhere.example" in entry["url"]
        for entry in actions(tmp_path)
    )


def test_erp_address_must_be_on_the_allowlist(tmp_path):
    with pytest.raises(ValueError, match="allowlist"):
        ErpFormConnector("http://erp.example", allowed_hosts={"127.0.0.1"}, log_dir=tmp_path)


def test_erp_not_running_is_retried(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens here now
    with pytest.raises(DeliveryFailed) as failed:
        form_connector(f"http://127.0.0.1:{port}", tmp_path).deliver(BOOKING, "k")
    assert failed.value.retryable is True


def test_every_browser_action_is_logged(world, tmp_path):
    with erp(world) as (url, _):
        form_connector(url, tmp_path).deliver(BOOKING, "k")
    done = [entry["action"] for entry in actions(tmp_path)]
    assert {"goto", "fill", "click", "read_back", "booked"} <= set(done)
    assert done.count("fill") == len(BOOKING.as_json())


def test_markup_in_invoice_text_is_booked_as_text(world, tmp_path):
    odd = replace(BOOKING, number="<b>RE</b>&'1\"", reference="doc-<x>")
    with erp(world) as (url, engine):
        form_connector(url, tmp_path).deliver(odd, "k")
    (booked,) = stored(engine)
    assert (booked["number"], booked["reference"]) == ("<b>RE</b>&'1\"", "doc-<x>")


# --- the allowlist names host and port (hardening H6) ----------------------


def connector_for(url, hosts, tmp_path):
    return ErpFormConnector(url, allowed_hosts=set(hosts), log_dir=tmp_path)


def test_another_port_on_the_erps_host_is_blocked(tmp_path):
    connector = connector_for("http://127.0.0.1:8100", {"127.0.0.1:8100"}, tmp_path)
    assert connector.permits("http://127.0.0.1:8100/ui/bookings/new")
    assert not connector.permits("http://127.0.0.1:5432/")


def test_a_host_without_a_port_allows_only_its_default_port(tmp_path):
    connector = connector_for("https://erp.example", {"erp.example"}, tmp_path)
    assert connector.permits("https://erp.example/ui")
    assert connector.permits("http://erp.example/ui")
    assert not connector.permits("https://erp.example:8443/ui")


def test_host_names_are_compared_without_case(tmp_path):
    connector = connector_for("http://ERP.example:8100", {"erp.EXAMPLE:8100"}, tmp_path)
    assert connector.permits("http://Erp.Example:8100/ui")


def test_ipv6_host_and_port(tmp_path):
    connector = connector_for("http://[::1]:8100", {"[::1]:8100"}, tmp_path)
    assert connector.permits("http://[::1]:8100/ui")
    assert not connector.permits("http://[::1]:8101/ui")


def test_erp_on_a_port_the_allowlist_does_not_name_is_refused_at_start(tmp_path):
    """An allowlist written as a bare host name used to allow every port."""
    with pytest.raises(ValueError, match="127.0.0.1:8100"):
        connector_for("http://127.0.0.1:8100", {"127.0.0.1"}, tmp_path)


def test_default_allowlist_is_the_erp_screens_host_and_port(monkeypatch, tmp_path):
    from app.deliver import build_connectors
    from app.settings import Settings

    monkeypatch.setenv("ERP_CHANNEL", "form")
    monkeypatch.setenv("ERP_UI_URL", "http://127.0.0.1:8100")
    monkeypatch.setenv("BROWSER_LOG_DIR", str(tmp_path))
    for name in ("BROWSER_ALLOWED_HOSTS", "ERP_URL", "EXPORT_DIR", "WEBHOOK_URL"):
        monkeypatch.delenv(name, raising=False)
    (connector,) = build_connectors(Settings.from_env())
    assert connector.permits("http://127.0.0.1:8100/ui")
    assert not connector.permits("http://127.0.0.1:8000/documents")


def test_a_credit_note_is_booked_through_the_form_as_through_the_api(world, tmp_path):
    credit = replace(
        BOOKING,
        number="GS-2026-00003",
        reference="doc-3",
        document_type="381",
        preceding_invoice="RE-2026-00042",
        po_number=None,
    )
    with erp(world) as (url, engine):
        form_connector(url, tmp_path).deliver(credit, "k")
    api_engine = erp_engine(world)
    ErpApiConnector(TestClient(create_erp(api_engine))).deliver(credit, "k")
    (by_form,), (by_api,) = stored(engine), stored(api_engine)
    assert (by_form["document_type"], by_form["preceding_invoice"]) == ("381", "RE-2026-00042")
    assert {k: v for k, v in by_form.items() if k not in NOT_COMPARED} == {
        k: v for k, v in by_api.items() if k not in NOT_COMPARED
    }
