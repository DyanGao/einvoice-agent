"""Booking an invoice in the mock ERP, shared by its API and its HTML screens."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from mock_erp import db


class Booking(BaseModel):
    """An invoice to book, as a connector sends it."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: str
    number: str
    issue_date: date
    due_date: date | None = None
    currency: str
    net_total: Decimal
    vat_total: Decimal | None = None
    gross_total: Decimal
    amount_due: Decimal
    po_number: str | None = None
    iban: str | None = None
    # The sender's own reference for the invoice.
    reference: str
    # 380 invoice, 381 credit note: the amounts are positive either way.
    document_type: Literal["380", "381"] = "380"
    # A credit note's invoice, by its number.
    preceding_invoice: str | None = None


def _cents_or_none(amount: Decimal | None) -> int | None:
    return None if amount is None else db.cents(amount)


def _booking_columns(booking: Booking) -> dict:
    return {
        "supplier_id": booking.supplier_id,
        "number": booking.number,
        "issue_date": booking.issue_date,
        "due_date": booking.due_date,
        "currency": booking.currency,
        "net_cents": db.cents(booking.net_total),
        "vat_cents": _cents_or_none(booking.vat_total),
        "gross_cents": db.cents(booking.gross_total),
        "amount_due_cents": db.cents(booking.amount_due),
        "po_number": booking.po_number,
        "iban": booking.iban,
        "reference": booking.reference,
        "document_type": booking.document_type,
        "preceding_invoice": booking.preceding_invoice,
    }


def credited_cents(active: Session, row: db.BookedInvoiceRow) -> int:
    """What the supplier's booked credit notes naming this invoice take back."""
    if row.document_type != "381":
        credited = active.scalars(
            select(db.BookedInvoiceRow.gross_cents).where(
                db.BookedInvoiceRow.supplier_id == row.supplier_id,
                db.BookedInvoiceRow.document_type == "381",
                db.BookedInvoiceRow.preceding_invoice == row.number,
            )
        )
        return sum(credited)
    return 0


def booked_json(row: db.BookedInvoiceRow, active: Session | None = None) -> dict:
    """A booking; with a session, an invoice also says what of it is still
    open after the credit notes booked against it."""
    open_amount = None
    if active is not None and row.document_type != "381":
        open_amount = db.euros(row.amount_due_cents - credited_cents(active, row))
    return {
        "id": row.id,
        "supplier_id": row.supplier_id,
        "number": row.number,
        "issue_date": row.issue_date.isoformat(),
        "due_date": None if row.due_date is None else row.due_date.isoformat(),
        "currency": row.currency,
        "net_total": db.euros(row.net_cents),
        "vat_total": None if row.vat_cents is None else db.euros(row.vat_cents),
        "gross_total": db.euros(row.gross_cents),
        "amount_due": db.euros(row.amount_due_cents),
        "po_number": row.po_number,
        "iban": row.iban,
        "reference": row.reference,
        "channel": row.channel,
        "booked_at": row.booked_at,
        "document_type": row.document_type,
        "preceding_invoice": row.preceding_invoice,
        "open_amount": open_amount,
    }


def book(active: Session, booking: Booking, key: str, channel: str) -> tuple[dict, bool]:
    """Book once per key: the booking, and whether it is new. The same key
    with other content is a sender's mistake, not a second invoice."""
    try:
        columns = _booking_columns(booking)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    existing = active.scalar(
        select(db.BookedInvoiceRow).where(db.BookedInvoiceRow.idempotency_key == key)
    )
    if existing is not None:
        if any(getattr(existing, name) != value for name, value in columns.items()):
            raise HTTPException(409, f"key {key} was already used for a different booking")
        return booked_json(existing, active), False
    if active.get(db.SupplierRow, booking.supplier_id) is None:
        raise HTTPException(422, f"no supplier {booking.supplier_id}")
    row = db.BookedInvoiceRow(
        idempotency_key=key,
        channel=channel,
        booked_at=datetime.now(UTC).isoformat(timespec="seconds"),
        **columns,
    )
    active.add(row)
    active.commit()
    return booked_json(row, active), True
