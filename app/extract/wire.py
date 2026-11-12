"""The JSON shape the model is asked to return, and its conversion to a draft.

Kept separate from InvoiceDraft on purpose: the model writes strings, and the
conversion decides what counts as a usable amount or date. A value that does
not parse becomes None — a missing field goes to review, a mangled one would
go unnoticed.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict

from app.extract.draft import InvoiceDraft, LineDraft, PartyDraft, VatBreakdownDraft
from app.models import DocumentFormat

_WHITESPACE = re.compile(r"\s+")
_PLAIN_DECIMAL = re.compile(r"-?\d+(\.\d+)?")


class _Wire(BaseModel):
    # Structured outputs require additionalProperties: false on every object.
    model_config = ConfigDict(extra="forbid")


class WireLine(_Wire):
    line_id: str | None
    description: str | None
    quantity: str | None
    unit_price: str | None
    net: str | None
    vat_rate: str | None


class WireVatGroup(_Wire):
    category: str | None
    rate: str | None
    taxable_amount: str | None
    tax_amount: str | None


class WireInvoice(_Wire):
    number: str | None
    issue_date: str | None
    due_date: str | None
    type_code: str | None
    currency: str | None
    buyer_reference: str | None
    po_ref: str | None
    seller_name: str | None
    seller_vat_id: str | None
    buyer_name: str | None
    buyer_vat_id: str | None
    payment_ibans: list[str]
    lines: list[WireLine]
    vat_breakdown: list[WireVatGroup]
    line_net_total: str | None
    net_total: str | None
    vat_total: str | None
    gross_total: str | None
    amount_due: str | None


def _amount(value: str | None) -> Decimal | None:
    if value is None or not _PLAIN_DECIMAL.fullmatch(value.strip()):
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None


def _date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _text(value: str | None) -> str | None:
    return (value.strip() or None) if value is not None else None


def _compact(value: str | None) -> str | None:
    return (_WHITESPACE.sub("", value).upper() or None) if value is not None else None


def to_draft(wire: WireInvoice, source_format: DocumentFormat) -> InvoiceDraft:
    ibans = dict.fromkeys(filter(None, map(_compact, wire.payment_ibans)))
    return InvoiceDraft(
        number=_text(wire.number),
        issue_date=_date(wire.issue_date),
        type_code=_text(wire.type_code),
        currency=_compact(wire.currency),
        due_date=_date(wire.due_date),
        buyer_reference=_text(wire.buyer_reference),
        po_ref=_text(wire.po_ref),
        seller=PartyDraft(name=_text(wire.seller_name), vat_id=_compact(wire.seller_vat_id)),
        buyer=PartyDraft(name=_text(wire.buyer_name), vat_id=_compact(wire.buyer_vat_id)),
        payment_ibans=tuple(ibans),
        lines=tuple(
            LineDraft(
                line_id=_text(line.line_id),
                description=_text(line.description),
                quantity=_amount(line.quantity),
                unit_price=_amount(line.unit_price),
                net=_amount(line.net),
                vat_rate=_amount(line.vat_rate),
            )
            for line in wire.lines
        ),
        vat_breakdown=tuple(
            VatBreakdownDraft(
                category=_text(group.category),
                rate=_amount(group.rate),
                taxable_amount=_amount(group.taxable_amount),
                tax_amount=_amount(group.tax_amount),
            )
            for group in wire.vat_breakdown
        ),
        line_net_total=_amount(wire.line_net_total),
        net_total=_amount(wire.net_total),
        vat_total=_amount(wire.vat_total),
        gross_total=_amount(wire.gross_total),
        amount_due=_amount(wire.amount_due),
        source_format=source_format,
    )
