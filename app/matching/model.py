"""What matching works with and what it returns.

A match is a set of allocations rather than one row with one order, one
delivery and one bank transaction (decision D7): an order can be invoiced in
parts, an invoice paid in instalments, and one transfer can settle several
invoices. Each payment therefore says how much of a transaction it uses.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


@dataclass(frozen=True)
class OrderLine:
    position: int
    description: str
    quantity: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class DeliveryLine:
    po_position: int
    quantity: Decimal


@dataclass(frozen=True)
class Delivery:
    id: str
    po_number: str
    delivery_date: date
    lines: tuple[DeliveryLine, ...]


@dataclass(frozen=True)
class Order:
    number: str
    supplier_id: str
    order_date: date
    lines: tuple[OrderLine, ...]
    deliveries: tuple[Delivery, ...] = ()


@dataclass(frozen=True)
class BankTransaction:
    id: str
    booking_date: date
    # Paid out to the counterparty; negative: received from it (a refund).
    amount: Decimal
    counterparty_name: str
    counterparty_iban: str
    purpose: str


@dataclass(frozen=True)
class PeerInvoice:
    """Another invoice of the same supplier that is on file. Needed to tell
    whose payment a transfer is when one transfer covers several invoices."""

    number: str
    gross_total: Decimal
    # A credit note (its total positive, D13): it can be offset against a
    # payment, and never pays one itself.
    credit: bool = False


@dataclass(frozen=True)
class Payment:
    bank_transaction_id: str
    amount: Decimal  # the part of that transaction that pays this invoice
    # Set when this part was not paid but settled by a credit note offset in
    # the transfer: its number (v1.0 Stage 5).
    credit_note: str | None = None


class Basis(StrEnum):
    """Why a part of the match was made, strongest first."""

    EXACT_REFERENCE = "exact_reference"  # the number is quoted as it is
    SIMILAR_REFERENCE = "similar_reference"  # quoted with a slip
    CONTENT = "content"  # no usable reference: lines or amount decided
    NONE = "none"  # nothing matched
    AMBIGUOUS = "ambiguous"  # several candidates fit equally well; none chosen


@dataclass(frozen=True)
class Match:
    supplier_id: str | None = None
    po_number: str | None = None
    po_basis: Basis = Basis.NONE
    delivery_id: str | None = None
    payments: tuple[Payment, ...] = ()
    payment_basis: Basis = Basis.NONE
    # For a person: what was decided and on what grounds, or why nothing was.
    explanation: tuple[str, ...] = ()
