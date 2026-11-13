"""What an extractor returns: an invoice in which any field may be missing.

Structured documents either parse into a complete Invoice or fail. Extraction
from PDFs and scans is different: a reader may find the total but not the due
date, and the review UI has to show exactly that. A draft becomes an Invoice
only once every mandatory field is present.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.models import DocumentFormat, Invoice
from app.models.invoice import Amount


class _Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PartyDraft(_Draft):
    name: str | None = None
    vat_id: str | None = None


class LineDraft(_Draft):
    line_id: str | None = None
    description: str | None = None
    quantity: Amount | None = None
    unit_price: Amount | None = None
    net: Amount | None = None
    vat_rate: Amount | None = None


class VatBreakdownDraft(_Draft):
    category: str | None = None
    rate: Amount | None = None
    taxable_amount: Amount | None = None
    tax_amount: Amount | None = None


class InvoiceDraft(_Draft):
    number: str | None = None
    issue_date: date | None = None
    type_code: str | None = None
    currency: str | None = None
    due_date: date | None = None
    buyer_reference: str | None = None
    po_ref: str | None = None
    seller: PartyDraft = PartyDraft()
    buyer: PartyDraft = PartyDraft()
    payment_ibans: tuple[str, ...] = ()
    lines: tuple[LineDraft, ...] = ()
    vat_breakdown: tuple[VatBreakdownDraft, ...] = ()
    line_net_total: Amount | None = None
    net_total: Amount | None = None
    vat_total: Amount | None = None
    gross_total: Amount | None = None
    amount_due: Amount | None = None
    source_format: DocumentFormat | None = None
    field_confidence: dict[str, float] = Field(default_factory=dict)
    # Dotted paths of fields that were filled in by a rule instead of being
    # read from the document (see complete.py).
    derived_fields: tuple[str, ...] = ()

    def to_invoice(self) -> Invoice:
        """Raises pydantic.ValidationError while mandatory fields are missing."""
        return Invoice.model_validate(self.model_dump(exclude={"derived_fields"}))
