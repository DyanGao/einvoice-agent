import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.erp import ErpUnavailable, HttpErpClient
from eval.generators.world import build_world
from mock_erp import seed
from mock_erp.main import create_app


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def erp(world):
    """The real client against the real mock ERP, in memory."""
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    seed.load(world, engine)
    return HttpErpClient(TestClient(create_app(engine)))


def failing(status: int | None) -> HttpErpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if status is None:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(status, text="<html>oops</html>")

    return HttpErpClient(
        httpx.Client(base_url="http://erp.invalid", transport=httpx.MockTransport(handler))
    )


def test_supplier_is_found_by_vat_id(erp, world):
    supplier = world.suppliers[0]
    found = erp.find_supplier(supplier.company.vat_id, "some other name")
    assert (found.id, found.ibans) == (supplier.id, supplier.ibans)


def test_supplier_without_vat_id_is_found_by_name_however_it_is_written(erp, world):
    supplier = world.suppliers[1]
    assert erp.find_supplier(None, supplier.company.name.upper()).id == supplier.id


def test_vat_id_nobody_has_falls_back_to_the_name(erp, world):
    supplier = world.suppliers[2]
    assert erp.find_supplier("DE000000000", supplier.company.name).id == supplier.id


def test_part_of_a_name_is_not_a_match(erp, world):
    first_word = world.suppliers[0].company.name.split()[0]
    assert erp.find_supplier(None, first_word) is None


@pytest.mark.parametrize(("vat_id", "name"), [(None, None), (None, "  "), ("DE000000000", "X")])
def test_unknown_supplier_is_none(erp, vat_id, name):
    assert erp.find_supplier(vat_id, name) is None


def test_purchase_order_comes_with_decimal_quantities_and_prices(erp, world):
    order = world.orders[0]
    found = erp.get_purchase_order(order.number)
    assert [(line.description, line.quantity, line.unit_price) for line in found.lines] == [
        (line.description, line.quantity, line.unit_price) for line in order.lines
    ]


@pytest.mark.parametrize(
    "number", ["PO-000000", "../suppliers", "..", "a/b", "", "PO-1?limit=1", "x#y"]
)
def test_order_number_that_does_not_exist_or_is_not_a_number_is_none(erp, number):
    assert erp.get_purchase_order(number) is None


@pytest.mark.parametrize("status", [None, 500, 401], ids=["unreachable", "error", "refused"])
def test_erp_that_does_not_answer_properly_is_reported_not_treated_as_empty(status):
    with pytest.raises(ErpUnavailable):
        failing(status).find_supplier("DE123456788", None)
    with pytest.raises(ErpUnavailable):
        failing(status).get_purchase_order("PO-1")


def test_answer_that_is_not_json_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>login</html>")

    client = HttpErpClient(
        httpx.Client(base_url="http://erp.invalid", transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ErpUnavailable):
        client.find_supplier("DE123456788", None)


def test_orders_of_a_supplier_come_with_their_deliveries(erp, world):
    supplier_id = world.deliveries[0].po_number and next(
        o.supplier_id for o in world.orders if o.number == world.deliveries[0].po_number
    )
    found = {order.number: order for order in erp.orders_of(supplier_id)}
    assert set(found) == {o.number for o in world.orders if o.supplier_id == supplier_id}
    expected = sorted(d.id for d in world.deliveries if d.po_number in found)
    assert sorted(d.id for order in found.values() for d in order.deliveries) == expected


def test_order_lines_and_delivery_quantities_are_decimals(erp, world):
    delivery = world.deliveries[0]
    order = next(o for o in world.orders if o.number == delivery.po_number)
    found = next(o for o in erp.orders_of(order.supplier_id) if o.number == order.number)
    assert [(line.position, line.quantity, line.unit_price) for line in found.lines] == [
        (line.position, line.quantity, line.unit_price) for line in order.lines
    ]
    found_delivery = next(d for d in found.deliveries if d.id == delivery.id)
    assert [(line.po_position, line.quantity) for line in found_delivery.lines] == [
        (line.po_position, line.quantity) for line in delivery.lines
    ]


def test_transactions_to_a_suppliers_accounts(erp, world):
    supplier = world.suppliers[0]
    expected = sorted(
        (tx.id, tx.amount)
        for tx in world.bank_transactions
        if tx.counterparty_iban in supplier.ibans
    )
    found = erp.transactions_to(supplier.ibans)
    assert sorted((tx.id, tx.amount) for tx in found) == expected


def test_lists_longer_than_one_page_are_read_completely(erp, world, monkeypatch):
    from app.erp import client

    monkeypatch.setattr(client, "PAGE", 2)
    supplier = max(
        world.suppliers,
        key=lambda s: sum(tx.counterparty_iban in s.ibans for tx in world.bank_transactions),
    )
    expected = sum(tx.counterparty_iban in supplier.ibans for tx in world.bank_transactions)
    assert expected > 4
    assert len(erp.transactions_to(supplier.ibans)) == expected


@pytest.mark.parametrize("status", [None, 500], ids=["unreachable", "error"])
def test_candidate_lookups_report_an_erp_that_does_not_answer(status):
    with pytest.raises(ErpUnavailable):
        failing(status).orders_of("S001")
    with pytest.raises(ErpUnavailable):
        failing(status).transactions_to(["DE89370400440532013000"])
