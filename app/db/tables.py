"""Persistence schema. Changes here need an Alembic migration (make db-check)."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, LargeBinary, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.models import Invoice

Json = JSON().with_variant(JSONB(), "postgresql")


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class DocumentRow(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str | None] = mapped_column(String(255))
    format: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), index=True)
    error: Mapped[str | None]
    content: Mapped[bytes] = mapped_column(LargeBinary)
    # pending | approved | not_required (see app.db.review.ReviewState)
    review_state: Mapped[str] = mapped_column(String(20), index=True, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    invoice: Mapped["InvoiceRow | None"] = relationship(back_populates="document")
    draft: Mapped["DraftRow | None"] = relationship(back_populates="document")
    validation: Mapped["ValidationRow | None"] = relationship(back_populates="document")
    audit_events: Mapped[list["AuditEventRow"]] = relationship(
        back_populates="document", order_by="AuditEventRow.seq"
    )
    findings: Mapped[list["FindingRow"]] = relationship(
        back_populates="document", order_by="FindingRow.position", cascade="all, delete-orphan"
    )


class InvoiceRow(Base):
    """The normalized invoice. `data` holds the full model; the other columns
    are copies of the fields that later steps search by."""

    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), unique=True)
    number: Mapped[str] = mapped_column(String(255), index=True)
    issue_date: Mapped[date]
    seller_name: Mapped[str] = mapped_column(String(255))
    seller_vat_id: Mapped[str | None] = mapped_column(String(32), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    gross_total: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    data: Mapped[dict[str, Any]] = mapped_column(Json)

    document: Mapped[DocumentRow] = relationship(back_populates="invoice")


class DraftRow(Base):
    """The invoice as it stands in review. `original` is what the machine
    produced and never changes; `data` includes the reviewer's corrections."""

    __tablename__ = "drafts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), unique=True)
    original: Mapped[dict[str, Any]] = mapped_column(Json)
    data: Mapped[dict[str, Any]] = mapped_column(Json)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    document: Mapped[DocumentRow] = relationship(back_populates="draft")


class ValidationRow(Base):
    __tablename__ = "validation_results"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), unique=True)
    valid: Mapped[bool]
    scenario: Mapped[str | None] = mapped_column(String(255))
    violations: Mapped[list[dict[str, Any]]] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    document: Mapped[DocumentRow] = relationship(back_populates="validation")


class AuditEventRow(Base):
    """Append-only record of what happened to a document, and who did it."""

    __tablename__ = "audit_events"

    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    event: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(100))
    detail: Mapped[dict[str, Any]] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    document: Mapped[DocumentRow] = relationship(back_populates="audit_events")


class FindingRow(Base):
    """What the checks found on the document's current draft. Replaced as a
    whole every time the checks run; the audit trail keeps the history."""

    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    position: Mapped[int]
    type: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(10))
    message: Mapped[str]
    fields: Mapped[list[str]] = mapped_column(Json)
    detail: Mapped[dict[str, Any]] = mapped_column(Json)

    document: Mapped[DocumentRow] = relationship(back_populates="findings")


def invoice_columns(invoice: Invoice) -> dict:
    return {
        "number": invoice.number,
        "issue_date": invoice.issue_date,
        "seller_name": invoice.seller.name,
        "seller_vat_id": invoice.seller.vat_id,
        "currency": invoice.currency,
        "gross_total": invoice.gross_total,
        "data": invoice.model_dump(mode="json"),
    }
