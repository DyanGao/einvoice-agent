"""What the checks need to know beyond the invoice itself.

The checks do not look anything up. Whoever calls them resolves the supplier,
the purchase order and the invoices already on file (from the ERP, the
database) and passes them in, so every check stays a function of its inputs.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.checks.credit import CreditLink


class MasterData(StrEnum):
    """Whether the ERP could be asked about the supplier and the order."""

    AVAILABLE = "available"
    # No ERP is connected to this installation.
    NOT_CONFIGURED = "not_configured"
    # An ERP is connected but did not answer.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Supplier:
    """Master data of a supplier we already do business with."""

    id: str
    name: str
    vat_id: str | None = None
    ibans: tuple[str, ...] = ()


@dataclass(frozen=True)
class PoLine:
    description: str
    quantity: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class PurchaseOrder:
    number: str
    lines: tuple[PoLine, ...]


@dataclass(frozen=True)
class KnownLine:
    description: str
    quantity: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class KnownInvoice:
    """A document already on file, as far as duplicate detection and the link
    of a credit note to its invoice need it."""

    document_id: str
    seller_name: str | None
    seller_vat_id: str | None
    number: str | None
    issue_date: date | None
    gross_total: Decimal | None
    # 380 for an invoice, 381 for a credit note; None if it was not read.
    type_code: str | None = None
    # A person rejected it: not an invoice a credit note can correct.
    rejected: bool = False
    lines: tuple[KnownLine, ...] = ()


@dataclass(frozen=True)
class CheckSettings:
    # Unit price above the ordered price by more than this share is reported.
    price_tolerance: Decimal = Decimal("0.02")
    # Billed quantity above the ordered quantity by more than this share.
    quantity_tolerance: Decimal = Decimal("0")
    # Whether an invoice without a purchase order has to be looked at.
    po_required: bool = True


@dataclass(frozen=True)
class CheckContext:
    # Unless AVAILABLE, supplier and purchase_order say nothing: the checks
    # that need them do not run and one finding says so.
    master_data: MasterData = MasterData.AVAILABLE
    # Why the ERP did not answer, for the finding.
    master_data_problem: str | None = None
    # None: no supplier with this VAT ID or name is known.
    supplier: Supplier | None = None
    # None: no order could be found for the invoice.
    purchase_order: PurchaseOrder | None = None
    # Set when the order was found although the invoice does not quote its
    # number correctly: how it was found, for the reviewer.
    purchase_order_note: str | None = None
    # Set when no order was chosen because several fit or a similar number
    # could not be confirmed: why, for the reviewer.
    purchase_order_problem: str | None = None
    known_invoices: tuple[KnownInvoice, ...] = ()
    # The delivery the invoice was matched to, and the invoices on file that
    # were matched to the same one.
    delivery_id: str | None = None
    delivery_billed_by: tuple[KnownInvoice, ...] = ()
    # For a credit note: the invoice it corrects, or why none was found.
    credit: "CreditLink | None" = None
    settings: CheckSettings = field(default_factory=CheckSettings)
