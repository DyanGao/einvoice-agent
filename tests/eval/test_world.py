import json
from collections import Counter
from decimal import Decimal

import pytest

from app.checks import CheckContext, PoLine, PurchaseOrder, Supplier, run_checks
from app.checks.rules import german_vat_id_checksum_holds
from app.extract import InvoiceDraft
from app.extract.confidence import valid_iban
from app.models import DocumentFormat
from eval.generators import world as world_module
from eval.generators.world import (
    BANK_TRANSACTIONS,
    ORDERS,
    PAID_COMBINED,
    PAID_IN_PARTS,
    PAID_ONCE,
    PO_EXACT,
    PO_MISSING,
    PO_TYPO,
    SUPPLIERS,
    UNPAID,
    build_world,
    write_world,
)


@pytest.fixture(scope="module")
def world():
    return build_world()


def test_world_has_the_planned_size(world):
    assert (len(world.suppliers), len(world.orders), len(world.bank_transactions)) == (
        SUPPLIERS,
        ORDERS,
        BANK_TRANSACTIONS,
    )


def test_same_seed_builds_the_same_world(world):
    assert build_world() == world


def test_another_seed_builds_another_world(world):
    assert build_world(seed=1).invoices != world.invoices


def test_ids_are_unique(world):
    for ids in (
        [s.id for s in world.suppliers],
        [o.number for o in world.orders],
        [d.id for d in world.deliveries],
        [t.id for t in world.bank_transactions],
        [i.id for i in world.invoices],
        [(i.supplier_id, i.invoice.number) for i in world.invoices],
    ):
        assert len(ids) == len(set(ids))


def test_supplier_vat_ids_and_ibans_are_well_formed(world):
    for supplier in world.suppliers:
        assert german_vat_id_checksum_holds(supplier.company.vat_id)
        assert all(map(valid_iban, supplier.ibans))


def test_every_hard_case_occurs_often_enough_to_be_measured(world):
    po = Counter(entry.po_ref_kind for entry in world.invoices)
    paid = Counter(entry.payment_kind for entry in world.invoices)
    counts = [po[PO_EXACT], po[PO_TYPO], po[PO_MISSING]]
    counts += [paid[PAID_ONCE], paid[PAID_IN_PARTS], paid[PAID_COMBINED], paid[UNPAID]]
    assert min(counts) >= 30


def test_some_orders_are_split_and_some_have_no_delivery_yet(world):
    deliveries_per_order = Counter(delivery.po_number for delivery in world.deliveries)
    assert 2 in deliveries_per_order.values()
    assert len(deliveries_per_order) < len(world.orders)


def test_invoice_truth_points_at_its_suppliers_order_and_delivery(world):
    orders = {order.number: order for order in world.orders}
    deliveries = {delivery.id: delivery for delivery in world.deliveries}
    for entry in world.invoices:
        assert orders[entry.po_number].supplier_id == entry.supplier_id
        assert deliveries[entry.delivery_id].po_number == entry.po_number


def test_invoice_bills_exactly_what_its_delivery_delivered_at_the_ordered_price(world):
    orders = {order.number: order for order in world.orders}
    deliveries = {delivery.id: delivery for delivery in world.deliveries}
    for entry in world.invoices:
        ordered = {line.position: line for line in orders[entry.po_number].lines}
        delivered = deliveries[entry.delivery_id].lines
        billed = [(item.quantity, item.unit_price) for item in entry.invoice.items]
        assert billed == [
            (line.quantity, ordered[line.po_position].unit_price) for line in delivered
        ]


def test_deliveries_never_exceed_the_order(world):
    delivered: Counter = Counter()
    for delivery in world.deliveries:
        for line in delivery.lines:
            delivered[(delivery.po_number, line.po_position)] += line.quantity
    ordered = {(o.number, line.position): line.quantity for o in world.orders for line in o.lines}
    assert all(quantity <= ordered[key] for key, quantity in delivered.items())


def test_po_reference_on_the_invoice_matches_its_kind(world):
    numbers = {order.number for order in world.orders}
    for entry in world.invoices:
        ref = entry.invoice.po_ref
        if entry.po_ref_kind == PO_EXACT:
            assert ref == entry.po_number
        elif entry.po_ref_kind == PO_MISSING:
            assert ref is None
        else:
            # A typo is neither the right number nor, by accident, another order's.
            assert ref != entry.po_number
            assert ref not in numbers


def test_paid_invoices_are_paid_in_full_and_unpaid_ones_not_at_all(world):
    for entry in world.invoices:
        paid = sum((payment.amount for payment in entry.payments), Decimal(0))
        expected = Decimal(0) if entry.payment_kind == UNPAID else entry.invoice.gross_total
        assert paid == expected


def test_number_of_payments_matches_the_payment_kind(world):
    expected = {UNPAID: 0, PAID_ONCE: 1, PAID_COMBINED: 1, PAID_IN_PARTS: 2}
    assert all(len(entry.payments) == expected[entry.payment_kind] for entry in world.invoices)


def test_every_paying_transaction_is_used_up_exactly_by_the_invoices_it_pays(world):
    allocated: Counter = Counter()
    for entry in world.invoices:
        for payment in entry.payments:
            allocated[payment.bank_transaction_id] += payment.amount
    amounts = {tx.id: tx.amount for tx in world.bank_transactions}
    assert all(amounts[tx_id] == total for tx_id, total in allocated.items())


def test_a_combined_payment_covers_three_invoices_of_one_supplier(world):
    groups: dict[str, list] = {}
    for entry in world.invoices:
        if entry.payment_kind == PAID_COMBINED:
            groups.setdefault(entry.payments[0].bank_transaction_id, []).append(entry)
    assert groups
    for members in groups.values():
        assert (len(members), len({member.supplier_id for member in members})) == (3, 1)


def test_payments_go_to_the_suppliers_account(world):
    suppliers = {supplier.id: supplier for supplier in world.suppliers}
    transactions = {tx.id: tx for tx in world.bank_transactions}
    for entry in world.invoices:
        for payment in entry.payments:
            iban = transactions[payment.bank_transaction_id].counterparty_iban
            assert iban in suppliers[entry.supplier_id].ibans


def test_many_transactions_pay_no_invoice_at_all(world):
    paying = {p.bank_transaction_id for entry in world.invoices for p in entry.payments}
    assert len(world.bank_transactions) - len(paying) >= 100


def test_transaction_ids_follow_the_booking_date_not_the_invoices(world):
    dates = [tx.booking_date for tx in world.bank_transactions]
    assert dates == sorted(dates)


def test_about_a_third_of_the_invoices_is_held_out(world):
    share = sum(entry.split == "test" for entry in world.invoices) / len(world.invoices)
    assert 0.2 < share < 0.4


def test_a_clean_world_invoice_with_its_real_context_has_no_findings(world):
    """The checks and the world agree on what a normal invoice looks like."""
    suppliers = {supplier.id: supplier for supplier in world.suppliers}
    orders = {order.number: order for order in world.orders}
    for entry in world.invoices:
        if entry.po_ref_kind != PO_EXACT:
            continue
        supplier, order = suppliers[entry.supplier_id], orders[entry.po_number]
        context = CheckContext(
            supplier=Supplier(
                supplier.id, supplier.company.name, supplier.company.vat_id, supplier.ibans
            ),
            purchase_order=PurchaseOrder(
                order.number,
                tuple(PoLine(x.description, x.quantity, x.unit_price) for x in order.lines),
            ),
        )
        gold = entry.invoice.to_gold(DocumentFormat.UNKNOWN)
        assert run_checks(InvoiceDraft.model_validate(gold.model_dump()), context) == []


def test_ground_truth_is_written_next_to_the_erp_data_not_into_it(world, tmp_path):
    write_world(world, tmp_path)
    gold = [json.loads(line) for line in (tmp_path / "gold.jsonl").read_text().splitlines()]
    assert (len(gold), set(gold[0]["match"])) == (
        len(world.invoices),
        {"po_number", "delivery_id", "payments"},
    )
    erp_files = ("suppliers", "orders", "deliveries", "bank_transactions")
    erp_text = "".join((tmp_path / f"{name}.jsonl").read_text() for name in erp_files)
    assert world.invoices[0].id not in erp_text


def test_typo_changes_the_reference():
    import random

    rng = random.Random(1)  # noqa: S311
    assert all(world_module._typo("PO-100200", rng) != "PO-100200" for _ in range(200))
