"""What every output connector has in common.

A connector takes a booking — the invoice as the downstream system needs
it — and an idempotency key that is the same on every attempt for the same
invoice and connector. It either delivers, returning the downstream
system's reference for the booking, or raises DeliveryFailed, saying whether
trying again could help.
"""

from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(frozen=True)
class Booking:
    """An invoice to book. Amounts are decimal strings, dates ISO strings: it
    is stored as JSON before the first attempt and sent unchanged on every
    attempt after, so a retry can never send something else."""

    supplier_id: str
    number: str
    issue_date: str
    due_date: str | None
    currency: str
    net_total: str
    vat_total: str | None
    gross_total: str
    amount_due: str
    po_number: str | None
    iban: str | None
    # The application's own reference for the invoice: its document id.
    reference: str

    def as_json(self) -> dict:
        return asdict(self)


class DeliveryFailed(RuntimeError):
    """The connector could not deliver. `retryable`: the downstream system
    was not reachable or failed itself, so a later attempt may succeed; not
    retryable: it refused the booking, and the same booking will be refused
    again."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class Connector(Protocol):
    # Names the connector in deliveries and audit events.
    name: str

    def deliver(self, booking: Booking, key: str) -> str:
        """Deliver once per key; the downstream system's reference."""
        ...
