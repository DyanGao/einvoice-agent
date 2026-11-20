"""Persistence schema. Changes here need an Alembic migration (make db-check)."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DDL,
    JSON,
    DateTime,
    ForeignKey,
    LargeBinary,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.db import rls
from app.models import Invoice

Json = JSON().with_variant(JSONB(), "postgresql")


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


# Every installation has this tenant: what existed before tenants belongs to
# it, and until per-tenant settings exist (v1.0 Stage 2, increment 4) the
# ERP, connectors and decision settings in .env are its own.
DEFAULT_TENANT_ID = "default"


class TenantRow(Base):
    """A company served by this installation. Nothing of one tenant is visible
    to another: documents, their matches and bookings, tokens and sessions."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# Databases made from the tables (tests, the demo, the UI test server) get the
# default tenant too; on Postgres the migration inserts it.
event.listen(
    TenantRow.__table__,
    "after_create",
    DDL(
        "INSERT INTO tenants (id, slug, name, created_at) "
        "VALUES ('default', 'default', 'Default', CURRENT_TIMESTAMP)"
    ),
)


class DocumentRow(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    # No default: whoever stores a document must say whose it is.
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    # The same file sent to two companies is two documents.
    sha256: Mapped[str] = mapped_column(String(64))
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
    match: Mapped["MatchRow | None"] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    findings: Mapped[list["FindingRow"]] = relationship(
        back_populates="document", order_by="FindingRow.position", cascade="all, delete-orphan"
    )
    decision: Mapped["DecisionRow | None"] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    deliveries: Mapped[list["DeliveryRow"]] = relationship(
        back_populates="document", order_by="DeliveryRow.connector"
    )

    __table_args__ = (UniqueConstraint("tenant_id", "sha256"),)


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
    # The signed-in user behind `actor`; None for the system, and for events from
    # before sign-in existed, whose actor is a name typed into the UI (unverified).
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
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


class MatchRow(Base):
    """The order, delivery and payments found for the document's current
    draft. Replaced every time matching runs."""

    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), unique=True)
    supplier_id: Mapped[str | None] = mapped_column(String(50))
    po_number: Mapped[str | None] = mapped_column(String(50))
    po_basis: Mapped[str] = mapped_column(String(20))
    delivery_id: Mapped[str | None] = mapped_column(String(50))
    # [{"bank_transaction_id": ..., "amount": "12.34"}]: how much of which
    # transfer pays this invoice.
    payments: Mapped[list[dict[str, str]]] = mapped_column(Json)
    payment_basis: Mapped[str] = mapped_column(String(20))
    explanation: Mapped[list[str]] = mapped_column(Json)

    document: Mapped[DocumentRow] = relationship(back_populates="match")


class DecisionRow(Base):
    """Whether the document's current draft may be booked without a person,
    and every reason it may not. Replaced every time the checks run."""

    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), unique=True)
    route: Mapped[str] = mapped_column(String(10))  # auto | review
    # [{"cause": ..., "message": ..., "fields": [...], "findings": [...]}]
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(Json)
    # Version of the thresholds the decision was made with.
    version: Mapped[str] = mapped_column(String(50))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    document: Mapped[DocumentRow] = relationship(back_populates="decision")


class DeliveryRow(Base):
    """One invoice on its way to one downstream system (app/delivery.py).
    The booking is stored before the first attempt and sent unchanged on
    every attempt, always with the same idempotency key."""

    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("document_id", "connector"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    connector: Mapped[str] = mapped_column(String(30))
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    # pending (to be tried, or retried at next_attempt_at) | delivered | failed
    status: Mapped[str] = mapped_column(String(10), index=True)
    booking: Mapped[dict[str, Any]] = mapped_column(Json)
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The downstream system's reference for the booking.
    external_ref: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[DocumentRow] = relationship(back_populates="deliveries")


class SeenBankTransactionRow(Base):
    """Bank transactions of the ERP the bank watch has already acted on
    (app/rematch.py). Only the last days are kept: the watch asks the ERP
    for transactions booked since a little before the latest one seen."""

    __tablename__ = "seen_bank_transactions"

    # Each tenant has its own ERP, so its own bank transactions.
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    id: Mapped[str] = mapped_column(String(50))
    booking_date: Mapped[date] = mapped_column(index=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (PrimaryKeyConstraint("tenant_id", "id"),)


class UserRow(Base):
    """A person who signs in, or the owner of an API token. `email` is stored
    lower-case. `password_hash` only for the built-in admin account(s); everyone
    else signs in through the identity provider. A person may work for several
    tenants, with a role in each (MembershipRow)."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str | None] = mapped_column(String(200))
    disabled: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # The identity provider's account, linked at the first sign-in through it;
    # from then on the user is found by it, not by the email.
    oidc_issuer: Mapped[str | None] = mapped_column(String(255))
    oidc_subject: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (UniqueConstraint("oidc_issuer", "oidc_subject"),)


class TenantSettingsRow(Base):
    """A tenant's own settings (app/tenants.py: TenantConfig), never a secret:
    a secret is named by the environment variable that holds it."""

    __tablename__ = "tenant_settings"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    config: Mapped[dict[str, Any]] = mapped_column(Json)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class MembershipRow(Base):
    """A user's role in one tenant: viewer | reviewer | admin."""

    __tablename__ = "memberships"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApiTokenRow(Base):
    """A token for scripts and integrations, acting as its user. Only a SHA-256
    of the token is kept; the token itself is shown once, when created."""

    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    # The tenant the token acts in: an integration belongs to one company.
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SessionRow(Base):
    """A signed-in browser. The cookie holds a random id; only its SHA-256 is
    kept, so a database copy cannot be used to take over a session."""

    __tablename__ = "sessions"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    # The tenant the person is working in.
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


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


# Databases made from these tables on Postgres get row-level security too;
# migrated ones get it from their migration (app/db/rls.py).
event.listen(Base.metadata, "after_create", rls.install)
