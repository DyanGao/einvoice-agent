"""Persistence schema. Changes here need an Alembic migration (make db-check)."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, LargeBinary, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    invoice: Mapped["InvoiceRow | None"] = relationship(back_populates="document")
    validation: Mapped["ValidationRow | None"] = relationship(back_populates="document")
    audit_events: Mapped[list["AuditEventRow"]] = relationship(
        back_populates="document", order_by="AuditEventRow.seq"
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
