"""Rule-based matching of an invoice to its order, delivery and payments.

This is the deterministic first pass of decision D6: it settles what the
references, the lines and the amounts settle, and says "ambiguous" or
"nothing found" for the rest instead of forcing a match. What it leaves open
goes to a person: no tool-calling agent follows it in v1 (decision D11).
"""

import re
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
from itertools import combinations
from typing import Protocol

from app.checks.context import Supplier
from app.extract.draft import InvoiceDraft
from app.matching.model import (
    BankTransaction,
    Basis,
    Delivery,
    Match,
    Order,
    Payment,
    PeerInvoice,
)
from app.texts import Text

# A reference with at most this many character edits is "the same, with a slip".
# A swapped pair of digits counts as two.
MAX_REFERENCE_EDITS = 2
# Payments are looked for from a little before the invoice date (prepaid on a
# pro forma) to this long after it.
PAYMENT_EARLIEST = timedelta(days=-3)
PAYMENT_LATEST = timedelta(days=365)
# One transfer is checked against the invoice plus up to this many others.
MAX_COMBINED_OTHERS = 4

_NOT_ALNUM = re.compile(r"[^a-z0-9]")
_SEPARATORS = re.compile(r"[\s,;]+")


class MatchingData(Protocol):
    """Where candidates come from; read-only (see app/erp/client.py)."""

    def find_supplier(self, vat_id: str | None, name: str | None) -> Supplier | None: ...

    def orders_of(self, supplier_id: str) -> list[Order]: ...

    def transactions_to(self, ibans: Sequence[str]) -> list[BankTransaction]: ...


def key(text: str | None) -> str:
    """A reference as compared: letters and digits only, and the letter O
    read as zero — the commonest slip of people and OCR alike."""
    return _NOT_ALNUM.sub("", (text or "").casefold()).replace("o", "0")


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b))
            )
        previous = current
    return previous[-1]


def _similar(a: str, b: str) -> bool:
    return bool(a) and bool(b) and a != b and edit_distance(a, b) <= MAX_REFERENCE_EDITS


# --- order and delivery -----------------------------------------------------


def _billed(invoice: InvoiceDraft) -> list[tuple[str, Decimal | None]]:
    return sorted((key(line.description), line.quantity) for line in invoice.lines)


def _delivered(order: Order, delivery: Delivery) -> list[tuple[str, Decimal | None]]:
    lines = {line.position: line for line in order.lines}
    return sorted(
        (key(lines[line.po_position].description), line.quantity)
        for line in delivery.lines
        if line.po_position in lines
    )


def _latest_before(
    fitting: list[tuple[Order, Delivery]], invoice: InvoiceDraft
) -> list[tuple[Order, Delivery]]:
    """Among deliveries of one order with identical content, keep the latest
    that is not after the invoice date. Left as it is when that decides nothing."""
    if invoice.issue_date is None:
        return fitting
    kept: list[tuple[Order, Delivery]] = []
    for order in {id(order): order for order, _ in fitting}.values():
        own = [delivery for owner, delivery in fitting if owner is order]
        earlier = [d for d in own if d.delivery_date <= invoice.issue_date]
        if len(own) > 1 and earlier:
            latest = max(d.delivery_date for d in earlier)
            own = [d for d in earlier if d.delivery_date == latest]
        kept += [(order, delivery) for delivery in own]
    return kept


def _match_order(
    invoice: InvoiceDraft, orders: list[Order]
) -> tuple[str | None, str | None, Basis, list[Text]]:
    """(order number, delivery id, basis, explanation)."""
    reference = key(invoice.po_ref)
    billed = _billed(invoice)

    # What speaks for each order: its number on the invoice, and a delivery
    # whose items and quantities are exactly what the invoice bills. Prices
    # are left out on purpose, so an invoice with a wrong price still finds
    # its order and the price check can do its work.
    exact = [order for order in orders if reference and key(order.number) == reference]
    similar = [order for order in orders if _similar(key(order.number), reference)]
    by_content = [
        (order, delivery)
        for order in orders
        for delivery in order.deliveries
        if billed and _delivered(order, delivery) == billed
    ]

    # An order delivered in equal halves has two deliveries with the same
    # content. The invoice belongs to the latest one not after its date.
    by_content = _latest_before(by_content, invoice)

    def delivery_of(order: Order) -> str | None:
        fitting = [delivery for owner, delivery in by_content if owner is order]
        return fitting[0].id if len(fitting) == 1 else None

    if len(exact) == 1:
        order = exact[0]
        delivery_id = delivery_of(order)
        notes = [Text("match.order_named", order=order.number)]
        notes.append(
            Text("match.delivery_exact", delivery=delivery_id)
            if delivery_id
            else Text("match.no_single_delivery")
        )
        return order.number, delivery_id, Basis.EXACT_REFERENCE, notes

    confirmed = [order for order in similar if delivery_of(order)]
    if len(confirmed) == 1:
        order = confirmed[0]
        return (
            order.number,
            delivery_of(order),
            Basis.SIMILAR_REFERENCE,
            [Text("match.order_slip", po_ref=invoice.po_ref, order=order.number)],
        )

    if len(by_content) == 1:
        order, delivery = by_content[0]
        note = (
            Text(
                "match.content_unknown_reference",
                po_ref=invoice.po_ref,
                delivery=delivery.id,
                order=order.number,
            )
            if reference
            else Text("match.content_no_reference", delivery=delivery.id, order=order.number)
        )
        return order.number, delivery.id, Basis.CONTENT, [note]
    if len(by_content) > 1:
        candidates = ", ".join(
            sorted(f"{order.number}/{delivery.id}" for order, delivery in by_content)
        )
        ambiguous = Text("match.deliveries_ambiguous", candidates=candidates)
        return None, None, Basis.AMBIGUOUS, [ambiguous]
    if len(similar) == 1:
        # A slip in the number and nothing to confirm it: named, not decided.
        return (
            None,
            None,
            Basis.AMBIGUOUS,
            [
                Text(
                    "match.order_similar_unconfirmed",
                    po_ref=invoice.po_ref,
                    order=similar[0].number,
                )
            ],
        )
    return None, None, Basis.NONE, [Text("match.no_order")]


# --- payments ---------------------------------------------------------------


def _reference_tokens(purpose: str) -> list[str]:
    """The parts of a payment reference that could be a document number."""
    tokens = [key(token) for token in _SEPARATORS.split(purpose)]
    return [token for token in tokens if any(char.isdigit() for char in token)]


def _reference_basis(
    transaction: BankTransaction, number: str, peer_numbers: set[str]
) -> tuple[Basis, bool, bool]:
    """(how the reference relates to this invoice, whether it quotes another
    invoice of the supplier exactly, whether some token is at least a slip of
    this invoice's number)."""
    tokens = _reference_tokens(transaction.purpose)
    quotes_peer = any(token in peer_numbers for token in tokens)
    if number and number in tokens:
        return Basis.EXACT_REFERENCE, quotes_peer, True
    # A token that is another invoice's number, exactly, is that invoice's.
    slips = [token for token in tokens if token not in peer_numbers and _similar(token, number)]
    # A slip of this invoice's number — unless the token is at least as close
    # to another invoice's number. A supplier's numbers often differ by one
    # digit, so "similar" alone would claim the neighbour's payment. Then the
    # reference says nothing either way and the amount has to decide.
    for token in slips:
        own = edit_distance(token, number)
        if all(edit_distance(token, peer) > own for peer in peer_numbers):
            return Basis.SIMILAR_REFERENCE, quotes_peer, True
    return Basis.CONTENT, quotes_peer, bool(slips)


_STRENGTH = {Basis.EXACT_REFERENCE: 3, Basis.SIMILAR_REFERENCE: 2, Basis.CONTENT: 1}


def _covers_with_peers(amount: Decimal, gross: Decimal, peers: Sequence[PeerInvoice]) -> bool:
    """Is the amount this invoice plus a few other invoices of the supplier?"""
    rest = amount - gross
    others = [peer.gross_total for peer in peers if peer.gross_total <= rest]
    return any(
        sum(group, Decimal(0)) == rest
        for size in range(1, MAX_COMBINED_OTHERS + 1)
        for group in combinations(others, size)
    )


def _match_payments(
    invoice: InvoiceDraft,
    transactions: list[BankTransaction],
    peers: Sequence[PeerInvoice],
) -> tuple[tuple[Payment, ...], Basis, list[Text]]:
    gross = invoice.gross_total
    if gross is None or gross <= 0:
        return (), Basis.NONE, [Text("match.no_total")]
    number = key(invoice.number)
    peer_numbers = {key(peer.number) for peer in peers} - {number, ""}

    # Transfers that pay this invoice alone: in the time window, and not
    # quoting some other invoice of the supplier unless they quote this one
    # exactly. A transfer for several invoices is looked at separately below.
    candidates: list[tuple[BankTransaction, Basis]] = []
    shared: list[tuple[BankTransaction, Basis]] = []
    for transaction in transactions:
        if invoice.issue_date is not None and not (
            invoice.issue_date + PAYMENT_EARLIEST
            <= transaction.booking_date
            <= invoice.issue_date + PAYMENT_LATEST
        ):
            continue
        basis, quotes_peer, names_us = _reference_basis(transaction, number, peer_numbers)
        if names_us:
            # For a shared transfer a loose resemblance is enough to look
            # closer; the amounts decide (see below).
            shared.append(
                (transaction, basis if basis is not Basis.CONTENT else Basis.SIMILAR_REFERENCE)
            )
        if not quotes_peer or basis is Basis.EXACT_REFERENCE:
            candidates.append((transaction, basis))

    # Every way the candidates could have paid this invoice, with how strongly
    # the references support it and a preference for the simpler story.
    explanations: list[tuple[int, int, tuple[Payment, ...], Basis, Text]] = []
    for transaction, basis in candidates:
        if transaction.amount == gross:
            explanations.append(
                (
                    _STRENGTH[basis],
                    3,
                    (Payment(transaction.id, gross),),
                    basis,
                    Text("match.transfer_exact", transfer=transaction.id),
                )
            )
    # One transfer for several invoices: it has to quote this invoice, at
    # least with a slip, and its amount has to be this invoice's total plus
    # the totals of a few others of the supplier. The sum is what protects
    # against claiming a neighbour's payment: a supplier's invoice numbers
    # often differ by a digit.
    for transaction, basis in shared:
        if transaction.amount > gross and _covers_with_peers(transaction.amount, gross, peers):
            explanations.append(
                (
                    _STRENGTH[basis],
                    2,
                    (Payment(transaction.id, gross),),
                    basis,
                    Text(
                        "match.transfer_shared",
                        transfer=transaction.id,
                        transfer_amount=transaction.amount,
                    ),
                )
            )
    smaller = [(tx, basis) for tx, basis in candidates if tx.amount < gross]
    for (first, first_basis), (second, second_basis) in combinations(smaller, 2):
        if first.amount + second.amount == gross:
            basis = min(first_basis, second_basis, key=_STRENGTH.__getitem__)
            ordered = sorted((first, second), key=lambda tx: (tx.booking_date, tx.id))
            explanations.append(
                (
                    _STRENGTH[basis],
                    1,
                    tuple(Payment(tx.id, tx.amount) for tx in ordered),
                    basis,
                    Text("match.transfers_two", first=ordered[0].id, second=ordered[1].id),
                )
            )

    if not explanations:
        return (), Basis.NONE, [Text("match.no_transfer")]
    twins = [peer.number for peer in peers if peer.gross_total == gross]
    if twins and all(entry[3] is Basis.CONTENT for entry in explanations):
        # Only the amount speaks for these transfers, and another invoice of
        # the supplier has the same amount: whose payment it is cannot be told.
        return (
            (),
            Basis.AMBIGUOUS,
            [Text("match.transfer_twin", number=twins[0])],
        )
    best = max((strength, simplicity) for strength, simplicity, *_ in explanations)
    top = [entry for entry in explanations if (entry[0], entry[1]) == best]
    if len(top) > 1:
        ids = sorted({payment.bank_transaction_id for entry in top for payment in entry[2]})
        return (), Basis.AMBIGUOUS, [Text("match.transfers_ambiguous", transfers=ids)]
    _, _, payments, basis, note = top[0]
    grounds = Text(f"grounds.{basis.value}")
    return payments, basis, [Text("match.payment_grounds", note=note, grounds=grounds)]


# --- the matcher ------------------------------------------------------------


def match_candidates(
    invoice: InvoiceDraft,
    supplier: Supplier,
    orders: list[Order],
    transactions: list[BankTransaction],
    peers: Sequence[PeerInvoice] = (),
) -> Match:
    """The match, given the supplier's orders and the transfers to it.
    `peers` are the supplier's other invoices on file."""
    po_number, delivery_id, po_basis, order_notes = _match_order(invoice, orders)
    payments, payment_basis, payment_notes = _match_payments(invoice, transactions, peers)
    return Match(
        supplier_id=supplier.id,
        po_number=po_number,
        po_basis=po_basis,
        delivery_id=delivery_id,
        payments=payments,
        payment_basis=payment_basis,
        explanation=(*order_notes, *payment_notes),
    )


def match_invoice(
    invoice: InvoiceDraft, data: MatchingData, peers: Sequence[PeerInvoice] = ()
) -> Match:
    """Look up the supplier, its orders and the transfers to it, and match."""
    supplier = data.find_supplier(invoice.seller.vat_id, invoice.seller.name)
    if supplier is None:
        return Match(explanation=(Text("match.no_supplier"),))
    return match_candidates(
        invoice,
        supplier,
        data.orders_of(supplier.id),
        data.transactions_to(supplier.ibans),
        peers,
    )


def match_by_exact_keys(invoice: InvoiceDraft, data: MatchingData) -> Match:
    """The baseline: only what is quoted exactly, character for character.
    An order by its number, a payment by the invoice number and the total."""
    supplier = data.find_supplier(invoice.seller.vat_id, invoice.seller.name)
    if supplier is None:
        return Match()
    orders = [order for order in data.orders_of(supplier.id) if order.number == invoice.po_ref]
    order = orders[0] if len(orders) == 1 else None
    delivery = order.deliveries[0] if order and len(order.deliveries) == 1 else None
    paying = [
        transaction
        for transaction in data.transactions_to(supplier.ibans)
        if invoice.number
        and invoice.number in transaction.purpose
        and transaction.amount == invoice.gross_total
    ]
    return Match(
        supplier_id=supplier.id,
        po_number=order.number if order else None,
        po_basis=Basis.EXACT_REFERENCE if order else Basis.NONE,
        delivery_id=delivery.id if delivery else None,
        payments=(Payment(paying[0].id, paying[0].amount),) if len(paying) == 1 else (),
        payment_basis=Basis.EXACT_REFERENCE if len(paying) == 1 else Basis.NONE,
    )
