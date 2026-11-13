from datetime import date
from decimal import Decimal

import pytest

from app.checks import Supplier
from app.extract import InvoiceDraft, LineDraft, PartyDraft
from app.matching import (
    BankTransaction,
    Basis,
    Delivery,
    DeliveryLine,
    Order,
    OrderLine,
    Payment,
    PeerInvoice,
    match_by_exact_keys,
    match_invoice,
)
from app.matching.matcher import key

IBAN = "DE89370400440532013000"
VAT_ID = "DE123456788"
SUPPLIER = Supplier(id="S1", name="Muster GmbH", vat_id=VAT_ID, ibans=(IBAN,))
ISSUED = date(2026, 3, 10)


class Erp:
    """Master data and bank transactions as the tests set them up."""

    def __init__(self, orders=(), transactions=(), supplier=SUPPLIER) -> None:
        self.supplier = supplier
        self.orders = list(orders)
        self.transactions = list(transactions)

    def find_supplier(self, vat_id, name):
        return self.supplier

    def orders_of(self, supplier_id):
        return self.orders

    def transactions_to(self, ibans):
        return [tx for tx in self.transactions if tx.counterparty_iban in ibans]


def order(number="PO-100200", lines=(("Papier", "10", "5.00"),), deliveries=None) -> Order:
    """An order; by default delivered completely in one delivery."""
    order_lines = tuple(
        OrderLine(position, description, Decimal(quantity), Decimal(price))
        for position, (description, quantity, price) in enumerate(lines, start=1)
    )
    if deliveries is None:
        deliveries = [
            ("LS-1", date(2026, 3, 8), [(line.position, line.quantity) for line in order_lines])
        ]
    return Order(
        number=number,
        supplier_id="S1",
        order_date=date(2026, 3, 1),
        lines=order_lines,
        deliveries=tuple(
            Delivery(
                id=delivery_id,
                po_number=number,
                delivery_date=delivered,
                lines=tuple(
                    DeliveryLine(position, Decimal(quantity)) for position, quantity in parts
                ),
            )
            for delivery_id, delivered, parts in deliveries
        ),
    )


def invoice(lines=(("Papier", "10", "5.00"),), gross="59.50", **fields) -> InvoiceDraft:
    base = dict(
        number="RE-2026-00123",
        issue_date=ISSUED,
        po_ref="PO-100200",
        seller=PartyDraft(name="Muster GmbH", vat_id=VAT_ID),
        lines=tuple(
            LineDraft(description=d, quantity=Decimal(q), unit_price=Decimal(p))
            for d, q, p in lines
        ),
        gross_total=Decimal(gross),
    )
    return InvoiceDraft(**(base | fields))


def transfer(
    amount="59.50", purpose="RE-2026-00123", tx_id="TX-1", on=date(2026, 3, 20), iban=IBAN
):
    return BankTransaction(tx_id, on, Decimal(amount), "Muster GmbH", iban, purpose)


def matched(inv, orders=(), transactions=(), peers=()):
    return match_invoice(inv, Erp(orders, transactions), peers)


# --- order and delivery -----------------------------------------------------


def test_order_named_exactly_is_matched_with_its_delivery():
    result = matched(invoice(), [order()])
    assert (result.po_number, result.delivery_id, result.po_basis) == (
        "PO-100200",
        "LS-1",
        Basis.EXACT_REFERENCE,
    )


@pytest.mark.parametrize(
    "written", ["PO-100020", "PO-10020", "PO-100201", "P0-1OO2OO", "po 100200"],
    ids=["swapped", "dropped", "replaced", "letter-o", "spacing"],
)  # fmt: skip
def test_order_reference_with_a_slip_still_finds_the_order(written):
    result = matched(invoice(po_ref=written), [order(), order("PO-777777", [("Toner", "1", "80")])])
    assert (result.po_number, result.delivery_id) == ("PO-100200", "LS-1")


def test_a_slip_is_only_trusted_when_a_delivery_confirms_it():
    unconfirmed = invoice(po_ref="PO-100201", lines=(("Stahl", "3", "9.00"),))
    result = matched(unconfirmed, [order()])
    assert (result.po_number, result.po_basis) == (None, Basis.AMBIGUOUS)
    assert "similar" in result.explanation[0]


def test_invoice_without_an_order_reference_is_matched_by_what_it_bills():
    result = matched(invoice(po_ref=None), [order(), order("PO-777777", [("Toner", "1", "80")])])
    assert (result.po_number, result.delivery_id, result.po_basis) == (
        "PO-100200",
        "LS-1",
        Basis.CONTENT,
    )


def test_wrong_price_does_not_stop_the_invoice_from_finding_its_order():
    overpriced = invoice(po_ref=None, lines=(("Papier", "10", "5.60"),))
    assert matched(overpriced, [order()]).po_number == "PO-100200"


def test_partial_delivery_is_matched_to_the_delivery_that_was_billed():
    split = order(
        lines=(("Papier", "10", "5.00"), ("Toner", "2", "80.00")),
        deliveries=[
            ("LS-1", date(2026, 3, 5), [(1, "10")]),
            ("LS-2", date(2026, 3, 9), [(2, "2")]),
        ],
    )
    result = matched(invoice(lines=(("Toner", "2", "80.00"),)), [split])
    assert (result.po_number, result.delivery_id) == ("PO-100200", "LS-2")


def test_two_equal_halves_are_told_apart_by_the_invoice_date():
    halves = order(
        lines=(("Papier", "10", "5.00"),),
        deliveries=[
            ("LS-1", date(2026, 3, 5), [(1, "5")]),
            ("LS-2", date(2026, 3, 15), [(1, "5")]),
        ],
    )
    half = (("Papier", "5", "5.00"),)
    first = matched(invoice(lines=half, issue_date=date(2026, 3, 6)), [halves])
    second = matched(invoice(lines=half, issue_date=date(2026, 3, 16)), [halves])
    assert (first.delivery_id, second.delivery_id) == ("LS-1", "LS-2")


def test_two_orders_that_fit_equally_well_are_not_chosen_between():
    twins = [
        order("PO-111111"),
        order("PO-222222", deliveries=[("LS-2", date(2026, 3, 8), [(1, "10")])]),
    ]
    result = matched(invoice(po_ref=None), twins)
    assert (result.po_number, result.po_basis) == (None, Basis.AMBIGUOUS)
    assert "PO-111111/LS-1" in result.explanation[0]


def test_invoice_that_fits_no_order_says_so_instead_of_forcing_one():
    stranger = invoice(po_ref=None, lines=(("Stahl", "3", "9.00"),))
    result = matched(stranger, [order()])
    assert (result.po_number, result.delivery_id, result.po_basis) == (None, None, Basis.NONE)


def test_named_order_without_a_fitting_delivery_is_matched_without_one():
    result = matched(invoice(lines=(("Papier", "4", "5.00"),)), [order()])
    assert (result.po_number, result.delivery_id) == ("PO-100200", None)


def test_unknown_supplier_matches_nothing():
    result = match_invoice(invoice(), Erp([order()], [transfer()], supplier=None))
    assert (result.supplier_id, result.po_number, result.payments) == (None, None, ())


# --- payments ---------------------------------------------------------------


def test_transfer_of_the_total_quoting_the_number_pays_the_invoice():
    result = matched(invoice(), transactions=[transfer()])
    assert (result.payments, result.payment_basis) == (
        (Payment("TX-1", Decimal("59.50")),),
        Basis.EXACT_REFERENCE,
    )


@pytest.mark.parametrize(
    "purpose",
    ["Rechnung RE-2026-00123", "RG RE-2026-00123 KD-NR 44711", "re 2026 00123", "RE-2O26-OO123"],
)
def test_number_is_recognised_however_the_reference_wraps_it(purpose):
    result = matched(invoice(number="RE-2026-00123"), transactions=[transfer(purpose=purpose)])
    assert len(result.payments) == 1


def test_reference_with_a_slip_and_the_right_amount_pays_the_invoice():
    result = matched(invoice(), transactions=[transfer(purpose="RE-2026-00132")])
    assert (len(result.payments), result.payment_basis) == (1, Basis.SIMILAR_REFERENCE)


def test_transfer_of_the_total_without_a_number_pays_the_invoice():
    result = matched(invoice(), transactions=[transfer(purpose="Zahlung")])
    assert (len(result.payments), result.payment_basis) == (1, Basis.CONTENT)


def test_invoice_paid_in_two_parts_gets_both_with_their_amounts():
    parts = [
        transfer("20.00", "Teilzahlung RE-2026-00123", "TX-1", date(2026, 3, 20)),
        transfer("39.50", "Rest RE-2026-00123", "TX-2", date(2026, 4, 2)),
    ]
    assert matched(invoice(), transactions=parts).payments == (
        Payment("TX-1", Decimal("20.00")),
        Payment("TX-2", Decimal("39.50")),
    )


def test_one_transfer_for_three_invoices_is_split_by_invoice_total():
    peers = [
        PeerInvoice("RE-2026-00130", Decimal("100.00")),
        PeerInvoice("RE-2026-00140", Decimal("40.50")),
    ]
    combined = transfer("200.00", "Sammelzahlung RE-2026-00123 / RE-2026-00130 / RE-2026-00140")
    result = matched(invoice(), transactions=[combined], peers=peers)
    assert result.payments == (Payment("TX-1", Decimal("59.50")),)
    assert "other invoices" in result.explanation[-1]


def test_combined_transfer_with_slips_in_every_number_is_still_recognised():
    peers = [
        PeerInvoice("RE-2026-00130", Decimal("100.00")),
        PeerInvoice("RE-2026-00140", Decimal("40.50")),
    ]
    combined = transfer("200.00", "RE-2026-00132, RE-2O26-00130, RE-2026-0140")
    assert len(matched(invoice(), transactions=[combined], peers=peers).payments) == 1


def test_combined_transfer_that_quotes_a_twin_exactly_is_the_twins_not_ours():
    """The twin has the same total and the next number; the sums would fit."""
    peers = [
        PeerInvoice("RE-2026-00124", Decimal("59.50")),
        PeerInvoice("RE-2026-00130", Decimal("100.00")),
    ]
    twins = transfer("159.50", "Sammelzahlung RE-2026-00124 / RE-2026-00130")
    assert matched(invoice(), transactions=[twins], peers=peers).payments == ()


def test_larger_transfer_that_no_set_of_invoices_explains_is_not_claimed():
    peers = [PeerInvoice("RE-2026-00130", Decimal("100.00"))]
    odd = transfer("500.00", "RE-2026-00123 und anderes")
    assert matched(invoice(), transactions=[odd], peers=peers).payments == ()


def test_unpaid_invoice_has_no_payment_and_says_so():
    other = transfer("75.00", "RE-2026-00999")
    result = matched(invoice(), transactions=[other])
    assert (result.payments, result.payment_basis) == ((), Basis.NONE)
    assert "no transfer" in result.explanation[-1]


def test_neighbours_payment_is_not_claimed_although_the_numbers_differ_by_one_digit():
    """A supplier's numbers run in sequence: 00124 is one edit from 00123."""
    peers = [PeerInvoice("RE-2026-00124", Decimal("59.50"))]
    neighbours = transfer(purpose="RE-2026-00124")
    assert matched(invoice(), transactions=[neighbours], peers=peers).payments == ()


def test_same_amount_without_a_number_is_undecidable_when_a_twin_invoice_exists():
    peers = [PeerInvoice("RE-2026-00124", Decimal("59.50"))]
    result = matched(invoice(), transactions=[transfer(purpose="Zahlung")], peers=peers)
    assert (result.payments, result.payment_basis) == ((), Basis.AMBIGUOUS)
    assert "RE-2026-00124" in result.explanation[-1]


def test_slip_that_is_as_close_to_a_twin_as_to_this_invoice_decides_nothing():
    peers = [PeerInvoice("RE-2026-00125", Decimal("59.50"))]
    between = transfer(purpose="RE-2026-00124")  # one edit from 00123 and from 00125
    assert matched(invoice(), transactions=[between], peers=peers).payment_basis is Basis.AMBIGUOUS


def test_two_transfers_that_fit_equally_well_are_not_chosen_between():
    same = [
        transfer(purpose="Zahlung", tx_id="TX-1"),
        transfer(purpose="Überweisung", tx_id="TX-2"),
    ]
    result = matched(invoice(), transactions=same)
    assert (result.payments, result.payment_basis) == ((), Basis.AMBIGUOUS)


def test_quoted_number_beats_a_bare_amount():
    both = [transfer(purpose="Zahlung", tx_id="TX-1"), transfer(tx_id="TX-2")]
    assert matched(invoice(), transactions=both).payments == (Payment("TX-2", Decimal("59.50")),)


@pytest.mark.parametrize(
    ("booked", "found"),
    [(date(2026, 3, 1), False), (date(2026, 3, 8), True), (date(2027, 3, 9), True),
     (date(2027, 3, 20), False)],
    ids=["long-before", "just-before", "a-year-later", "too-late"],
)  # fmt: skip
def test_only_transfers_around_and_after_the_invoice_date_count(booked, found):
    result = matched(invoice(), transactions=[transfer(on=booked)])
    assert bool(result.payments) is found


def test_transfer_to_another_account_is_not_this_suppliers_payment():
    elsewhere = transfer(iban="DE02120300000000202051")
    assert matched(invoice(), transactions=[elsewhere]).payments == ()


def test_invoice_without_a_total_is_not_matched_to_payments():
    assert matched(invoice(gross_total=None), transactions=[transfer()]).payments == ()


def test_reference_key_reads_letter_o_as_zero_and_ignores_punctuation():
    assert key("RE-2O26/00123 ") == key("re2026 00123") == "re202600123"


# --- baseline ---------------------------------------------------------------


def test_exact_key_baseline_finds_only_what_is_quoted_character_for_character():
    erp = Erp([order()], [transfer()])
    exact = match_by_exact_keys(invoice(), erp)
    slip = match_by_exact_keys(invoice(po_ref="PO-100201", number="RE-2026-00132"), erp)
    assert (exact.po_number, len(exact.payments)) == ("PO-100200", 1)
    assert (slip.po_number, slip.payments) == (None, ())
