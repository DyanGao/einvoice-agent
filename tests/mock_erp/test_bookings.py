"""The mock ERP's booking API: what the output connectors write to."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from eval.generators.world import build_world
from mock_erp import seed
from mock_erp.main import create_app


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    seed.load(build_world(), engine)
    return TestClient(create_app(engine))


BOOKING = {
    "supplier_id": "S001",
    "number": "RE-2026-00042",
    "issue_date": "2026-03-01",
    "due_date": "2026-03-31",
    "currency": "EUR",
    "net_total": "100.00",
    "vat_total": "19.00",
    "gross_total": "119.00",
    "amount_due": "119.00",
    "po_number": None,
    "iban": "DE89370400440532013000",
    "reference": "doc-1",
}


def book(client, body=BOOKING, key="key-1"):
    headers = {} if key is None else {"Idempotency-Key": key}
    return client.post("/booked-invoices", json=body, headers=headers)


def test_booking_is_stored_and_can_be_read_back(client):
    created = book(client)
    assert created.status_code == 201
    stored = client.get(f"/booked-invoices/{created.json()['id']}").json()
    assert {key: stored[key] for key in BOOKING} == BOOKING


def test_same_key_again_returns_the_first_booking_and_books_nothing_new(client):
    first = book(client).json()
    again = book(client)
    assert (again.status_code, again.json()["id"]) == (200, first["id"])
    assert len(client.get("/booked-invoices").json()["booked_invoices"]) == 1


def test_same_key_with_other_content_is_refused(client):
    book(client)
    changed = book(client, BOOKING | {"gross_total": "120.00"})
    assert changed.status_code == 409


def test_booking_without_a_key_is_refused(client):
    assert book(client, key=None).status_code == 400


def test_booking_for_an_unknown_supplier_is_refused(client):
    assert book(client, BOOKING | {"supplier_id": "S999"}).status_code == 422


def test_amount_with_fractions_of_a_cent_is_refused(client):
    assert book(client, BOOKING | {"gross_total": "119.001"}).status_code == 422


def test_bookings_are_listed_by_supplier(client):
    book(client)
    book(client, BOOKING | {"supplier_id": "S002"}, key="key-2")
    listed = client.get("/booked-invoices", params={"supplier_id": "S002"}).json()
    assert [entry["supplier_id"] for entry in listed["booked_invoices"]] == ["S002"]


def test_booking_records_the_channel_it_came_through(client):
    created = book(client).json()
    assert created["channel"] == "api"


CREDIT = {
    **BOOKING,
    "number": "GS-2026-00001",
    "net_total": "40.00",
    "vat_total": "7.60",
    "gross_total": "47.60",
    "amount_due": "47.60",
    "reference": "doc-2",
    "document_type": "381",
    "preceding_invoice": "RE-2026-00042",
}


def test_a_credit_note_lowers_what_is_open_of_its_invoice(client):
    invoice = book(client).json()
    assert (invoice["document_type"], invoice["open_amount"]) == ("380", "119.00")
    credit = book(client, CREDIT, "key-2").json()
    assert (credit["document_type"], credit["preceding_invoice"], credit["open_amount"]) == (
        "381",
        "RE-2026-00042",
        None,
    )
    assert client.get(f"/booked-invoices/{invoice['id']}").json()["open_amount"] == "71.40"


def test_another_document_type_is_refused(client):
    assert book(client, {**CREDIT, "document_type": "384"}).status_code == 422


def test_a_database_seeded_before_credit_notes_gets_their_columns(tmp_path):
    from sqlalchemy import inspect, text

    from mock_erp import db

    engine = create_engine(f"sqlite:///{tmp_path / 'erp.sqlite3'}")
    seed.load(build_world(), engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE booked_invoices DROP COLUMN document_type"))
        connection.execute(text("ALTER TABLE booked_invoices DROP COLUMN preceding_invoice"))
    db.upgrade(engine)
    db.upgrade(engine)  # nothing to do the second time
    columns = {c["name"] for c in inspect(engine).get_columns("booked_invoices")}
    assert {"document_type", "preceding_invoice"} <= columns
    assert book(TestClient(create_app(engine))).json()["document_type"] == "380"
