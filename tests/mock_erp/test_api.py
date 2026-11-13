import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from eval.generators.world import PAID_ONCE, build_world
from mock_erp import seed
from mock_erp.main import create_app


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def engine(world):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    seed.load(world, engine)
    return engine


@pytest.fixture(scope="module")
def client(engine):
    return TestClient(create_app(engine))


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_the_bare_address_leads_to_the_api_page_instead_of_not_found(client):
    response = client.get("/", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (307, "/docs")


def test_supplier_is_found_by_vat_id_with_its_ibans(client, world):
    supplier = world.suppliers[0]
    (found,) = client.get("/suppliers", params={"vat_id": supplier.company.vat_id}).json()[
        "suppliers"
    ]
    assert (found["id"], found["name"], found["ibans"]) == (
        supplier.id,
        supplier.company.name,
        list(supplier.ibans),
    )


def test_supplier_is_found_by_iban(client, world):
    supplier = world.suppliers[3]
    found = client.get("/suppliers", params={"iban": supplier.ibans[0]}).json()["suppliers"]
    assert [entry["id"] for entry in found] == [supplier.id]


def test_supplier_is_found_by_part_of_its_name_whatever_the_case(client, world):
    name = world.suppliers[5].company.name
    found = client.get("/suppliers", params={"q": name.split()[0].upper()}).json()["suppliers"]
    assert world.suppliers[5].id in [entry["id"] for entry in found]


def test_unknown_vat_id_finds_nobody(client):
    assert client.get("/suppliers", params={"vat_id": "DE000000000"}).json() == {"suppliers": []}


def test_supplier_by_id(client, world):
    assert client.get(f"/suppliers/{world.suppliers[0].id}").json()["id"] == world.suppliers[0].id
    assert client.get("/suppliers/S999").status_code == 404


def test_purchase_order_comes_with_its_lines_and_deliveries(client, world):
    delivery = world.deliveries[0]
    order = next(order for order in world.orders if order.number == delivery.po_number)
    body = client.get(f"/purchase-orders/{order.number}").json()
    assert (body["supplier_id"], len(body["lines"]), body["lines"][0]["unit_price"]) == (
        order.supplier_id,
        len(order.lines),
        str(order.lines[0].unit_price),
    )
    assert delivery.id in [entry["id"] for entry in body["deliveries"]]


def test_unknown_purchase_order_is_not_found(client):
    assert client.get("/purchase-orders/PO-000000").status_code == 404


def test_purchase_orders_of_one_supplier(client, world):
    supplier_id = world.orders[0].supplier_id
    expected = sorted(o.number for o in world.orders if o.supplier_id == supplier_id)
    found = client.get(
        "/purchase-orders", params={"supplier_id": supplier_id, "limit": 200}
    ).json()["purchase_orders"]
    assert [entry["number"] for entry in found] == expected


def test_purchase_orders_by_part_of_the_number(client, world):
    number = world.orders[7].number
    found = client.get("/purchase-orders", params={"q": number[3:8]}).json()["purchase_orders"]
    assert number in [entry["number"] for entry in found]


def test_deliveries_of_an_order(client, world):
    delivery = world.deliveries[0]
    found = client.get("/deliveries", params={"po_number": delivery.po_number}).json()["deliveries"]
    assert delivery.id in [entry["id"] for entry in found]
    assert all(entry["po_number"] == delivery.po_number for entry in found)


def test_payment_of_an_invoice_is_found_by_account_and_exact_amount(client, world):
    entry = next(entry for entry in world.invoices if entry.payment_kind == PAID_ONCE)
    supplier = next(s for s in world.suppliers if s.id == entry.supplier_id)
    found = client.get(
        "/bank-transactions",
        params={"iban": supplier.ibans[0], "amount": str(entry.invoice.gross_total)},
    ).json()["bank_transactions"]
    assert entry.payments[0].bank_transaction_id in [tx["id"] for tx in found]


def test_bank_transactions_by_amount_range_and_dates(client, world):
    found = client.get(
        "/bank-transactions",
        params={
            "amount_min": "100",
            "amount_max": "200",
            "date_from": "2026-03-01",
            "date_to": "2026-06-30",
            "limit": 200,
        },
    ).json()["bank_transactions"]
    expected = [
        tx.id
        for tx in world.bank_transactions
        if 100 <= tx.amount <= 200 and "2026-03-01" <= tx.booking_date.isoformat() <= "2026-06-30"
    ]
    assert [tx["id"] for tx in found] == expected


def test_bank_transactions_by_text_in_the_purpose(client, world):
    entry = next(e for e in world.invoices if e.payment_ref_kind == "exact" and e.payments)
    found = client.get("/bank-transactions", params={"q": entry.invoice.number}).json()[
        "bank_transactions"
    ]
    assert entry.payments[0].bank_transaction_id in [tx["id"] for tx in found]


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/suppliers", {"limit": 0}),
        ("/suppliers", {"limit": 201}),
        ("/bank-transactions", {"amount": "abc"}),
        ("/bank-transactions", {"amount": "1.234"}),
        ("/bank-transactions", {"date_from": "yesterday"}),
    ],
)
def test_invalid_parameters_are_rejected(client, path, params):
    assert client.get(path, params=params).status_code == 422


def test_paging_walks_through_all_suppliers_without_repeats(client, world):
    first = client.get("/suppliers", params={"limit": 30}).json()["suppliers"]
    rest = client.get("/suppliers", params={"limit": 30, "offset": 30}).json()["suppliers"]
    assert [s["id"] for s in first + rest] == [s.id for s in world.suppliers]


def test_the_api_cannot_change_anything(client):
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/suppliers").status_code == 405


def test_erp_holds_no_ground_truth_about_invoices(engine):
    tables = set(inspect(engine).get_table_names())
    assert "booked_invoices" in tables
    assert not {name for name in tables if "match" in name or "truth" in name}
    with engine.connect() as connection:
        assert connection.exec_driver_sql("select count(*) from booked_invoices").scalar() == 0


def test_seeding_again_replaces_the_content_instead_of_adding_to_it(world, engine):
    seed.load(world, engine)
    with engine.connect() as connection:
        count = connection.exec_driver_sql("select count(*) from suppliers").scalar()
    assert count == len(world.suppliers)
