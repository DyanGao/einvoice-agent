"""Normalized invoice: the EN 16931 subset every later step consumes.

BT/BG numbers refer to EN 16931 business terms. The model describes what a
document *says*; it does not enforce that totals add up — a document with
wrong arithmetic must still be representable so the checks can flag it.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.models.formats import DocumentFormat


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("amounts must be Decimal or str, not float")
    return value


Amount = Annotated[Decimal, BeforeValidator(_reject_float)]

# BT-3 type codes with a meaning of their own here.
INVOICE = "380"
CREDIT_NOTE = "381"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Party(_Model):
    name: str  # BT-27 seller / BT-44 buyer
    vat_id: str | None = None  # BT-31 seller / BT-48 buyer


class Line(_Model):
    line_id: str  # BT-126
    description: str  # BT-153
    quantity: Amount  # BT-129
    unit_price: Amount  # BT-146
    net: Amount  # BT-131
    vat_rate: Amount | None = None  # BT-152


class VatBreakdown(_Model):
    """One BG-23 group."""

    category: str  # BT-118
    rate: Amount | None = None  # BT-119
    taxable_amount: Amount  # BT-116
    tax_amount: Amount  # BT-117


class Invoice(_Model):
    number: str  # BT-1
    issue_date: date  # BT-2
    type_code: str  # BT-3 (380 invoice, 381 credit note, ...)
    currency: str = Field(min_length=3, max_length=3)  # BT-5
    due_date: date | None = None  # BT-9
    buyer_reference: str | None = None  # BT-10 (Leitweg-ID in XRechnung)
    po_ref: str | None = None  # BT-13
    # BT-25: the invoice a credit note (381) or corrected invoice (384) refers to.
    preceding_ref: str | None = None

    seller: Party
    buyer: Party
    # BT-84. A document may name several accounts (BG-17 repeats); all are kept
    # because any unknown one is a fraud signal.
    payment_ibans: tuple[str, ...] = ()

    lines: tuple[Line, ...]
    vat_breakdown: tuple[VatBreakdown, ...] = ()

    line_net_total: Amount  # BT-106
    net_total: Amount  # BT-109
    vat_total: Amount | None = None  # BT-110
    gross_total: Amount  # BT-112
    amount_due: Amount  # BT-115

    source_format: DocumentFormat
    field_confidence: dict[str, float] = Field(default_factory=dict)

    @property
    def is_credit_note(self) -> bool:
        """Its amounts are positive, as on an invoice: the sign comes from the type
        (ARCHITECTURE.md, D13)."""
        return self.type_code == CREDIT_NOTE
