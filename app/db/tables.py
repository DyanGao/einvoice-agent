"""Persistence schema. Changes here need an Alembic migration (make db-check)."""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    DDL,
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    event,
    false,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app import crypto
from app.db import rls
from app.db.sealed import Sealed
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


class TenantKeyRow(Base):
    """A tenant's data key, wrapped by the master key (app/crypto.py). Not
    under row-level security: without the master key it opens nothing, and
    the keyring reads it outside any tenant's transaction."""

    __tablename__ = "tenant_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    version: Mapped[int]
    wrapped: Mapped[bytes] = mapped_column(LargeBinary)
    # Which master key wrapped it (MasterKey.id), to say so when another is set.
    master_id: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("tenant_id", "version"),)


class DocumentRow(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    # No default: whoever stores a document must say whose it is.
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    # The same file sent to two companies is two documents.
    sha256: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str | None] = mapped_column(Sealed("documents.filename", "text"))
    format: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), index=True)
    error: Mapped[str | None] = mapped_column(Sealed("documents.error", "text"))
    # The original file, sealed with the tenant's data key (app/crypto.py,
    # v1.0 Stage 6); `content` below is the file itself.
    sealed_content: Mapped[bytes] = mapped_column("content", LargeBinary)
    # pending | approved | not_required (see app.db.review.ReviewState)
    review_state: Mapped[str] = mapped_column(String(20), index=True, default="pending")
    # The newest hash of its history's chain (app/db/chain.py); None before it.
    audit_head: Mapped[str | None] = mapped_column(String(64))
    # Kept whatever its retention end (app/retention.py); why is in its history.
    legal_hold: Mapped[bool] = mapped_column(default=False, server_default=false())
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
    # For a credit note: the invoice it corrects (Stage 5).
    credit_link: Mapped["CreditLinkRow | None"] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        foreign_keys="CreditLinkRow.document_id",
    )
    # For an invoice: the credit notes linked to it.
    credited_by: Mapped[list["CreditLinkRow"]] = relationship(
        foreign_keys="CreditLinkRow.invoice_document_id", viewonly=True
    )

    __table_args__ = (UniqueConstraint("tenant_id", "sha256"),)

    @property
    def content(self) -> bytes:
        """The original as received, opened on first use."""
        plain = getattr(self, "_plain_content", None)
        if plain is None:
            plain = crypto.open_sealed(self.sealed_content, CONTENT_PLACE)
            self._plain_content = plain
        return plain

    @content.setter
    def content(self, value: bytes) -> None:
        # Sealed when written (_seal_content): the tenant may be set after it.
        self._plain_content = value
        self._needs_seal = True
        self.sealed_content = value


CONTENT_PLACE = "documents.content"


@event.listens_for(DocumentRow, "before_insert")
@event.listens_for(DocumentRow, "before_update")
def _seal_content(mapper, connection, target: DocumentRow) -> None:
    if getattr(target, "_needs_seal", False):
        target.sealed_content = crypto.seal(target.tenant_id, target._plain_content, CONTENT_PLACE)
        target._needs_seal = False


class InvoiceRow(Base):
    """The normalized invoice: the full model, sealed. (Until v1.0 Stage 6 it
    had plain copies of number, seller and totals; nothing searched by them.)"""

    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), unique=True)
    data: Mapped[dict[str, Any]] = mapped_column(Sealed("invoices.data"))

    document: Mapped[DocumentRow] = relationship(back_populates="invoice")


class DraftRow(Base):
    """The invoice as it stands in review. `original` is what the machine
    produced and never changes; `data` includes the reviewer's corrections."""

    __tablename__ = "drafts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), unique=True)
    original: Mapped[dict[str, Any]] = mapped_column(Sealed("drafts.original"))
    data: Mapped[dict[str, Any]] = mapped_column(Sealed("drafts.data"))
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
    violations: Mapped[list[dict[str, Any]]] = mapped_column(
        Sealed("validation_results.violations")
    )
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
    detail: Mapped[dict[str, Any]] = mapped_column(Sealed("audit_events.detail"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # The hash chain over the document's history (app/db/chain.py): the hash
    # before this one, and this event's. None for events from before the chain.
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str | None] = mapped_column(String(64))

    document: Mapped[DocumentRow] = relationship(back_populates="audit_events")


class FindingRow(Base):
    """What the checks found on the document's current draft. Replaced as a
    whole every time the checks run; the audit trail keeps the history."""

    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    position: Mapped[int]
    # The message's code and parameters (app/texts.py), for the UI's language;
    # None for findings from before v1.0 Stage 4, which show `message`.
    text: Mapped[dict[str, Any] | None] = mapped_column(Sealed("findings.text"))
    type: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Sealed("findings.message", "text"))
    fields: Mapped[list[str]] = mapped_column(Json)
    detail: Mapped[dict[str, Any]] = mapped_column(Sealed("findings.detail"))

    document: Mapped[DocumentRow] = relationship(back_populates="findings")


class CreditLinkRow(Base):
    """The invoice a credit note corrects, as the checks found it, or why none
    was (app/checks/credit.py). Replaced every time the checks run; how much of
    an invoice is credited adds up its links."""

    __tablename__ = "credit_links"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), unique=True)
    # None: not found, or several fit.
    invoice_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), index=True)
    basis: Mapped[str] = mapped_column(String(20))
    explanation: Mapped[str] = mapped_column(Sealed("credit_links.explanation", "text"))
    explanation_text: Mapped[dict[str, Any] | None] = mapped_column(
        Sealed("credit_links.explanation_text")
    )

    document: Mapped[DocumentRow] = relationship(
        back_populates="credit_link", foreign_keys=[document_id]
    )
    invoice: Mapped[DocumentRow | None] = relationship(
        foreign_keys=[invoice_document_id], viewonly=True
    )


class MatchRow(Base):
    """The order, delivery and payments found for the document's current
    draft. Replaced every time matching runs."""

    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), unique=True)
    supplier_id: Mapped[str | None] = mapped_column(String(50))
    po_number: Mapped[str | None] = mapped_column(Sealed("matches.po_number", "text"))
    po_basis: Mapped[str] = mapped_column(String(20))
    delivery_id: Mapped[str | None] = mapped_column(String(50))
    # [{"bank_transaction_id": ..., "amount": "12.34"}]: how much of which
    # transfer pays this invoice.
    payments: Mapped[list[dict[str, str]]] = mapped_column(Sealed("matches.payments"))
    payment_basis: Mapped[str] = mapped_column(String(20))
    explanation: Mapped[list[str]] = mapped_column(Sealed("matches.explanation"))
    # Each explanation's code and parameters, in the same order; None where a
    # line has none (rows from before v1.0 Stage 4).
    explanation_texts: Mapped[list[dict[str, Any] | None] | None] = mapped_column(
        Sealed("matches.explanation_texts")
    )

    document: Mapped[DocumentRow] = relationship(back_populates="match")


class DecisionRow(Base):
    """Whether the document's current draft may be booked without a person,
    and every reason it may not. Replaced every time the checks run."""

    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), unique=True)
    route: Mapped[str] = mapped_column(String(10))  # auto | review
    # [{"cause": ..., "message": ..., "fields": [...], "findings": [...]}]
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(Sealed("decisions.reasons"))
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
    booking: Mapped[dict[str, Any]] = mapped_column(Sealed("deliveries.booking"))
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The downstream system's reference for the booking.
    external_ref: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(Sealed("deliveries.last_error", "text"))
    error_text: Mapped[dict[str, Any] | None] = mapped_column(Sealed("deliveries.error_text"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[DocumentRow] = relationship(back_populates="deliveries")


class DeletionLogRow(Base):
    """A document deleted after its retention end (app/retention.py): what it
    was (its original's SHA-256, its history's newest hash and length), when
    it could go and when it went. Append-only: the tenant role may read and
    add, and on Postgres a trigger refuses every change; here too."""

    __tablename__ = "deletion_log"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    # No foreign key: the document is gone.
    document_id: Mapped[str] = mapped_column(String(32))
    sha256: Mapped[str] = mapped_column(String(64))
    audit_head: Mapped[str | None] = mapped_column(String(64))
    events: Mapped[int]
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    retention_end: Mapped[date]
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    deleted_by: Mapped[str] = mapped_column(String(100))


@event.listens_for(DeletionLogRow, "before_update")
@event.listens_for(DeletionLogRow, "before_delete")
def _append_only(mapper, connection, target: DeletionLogRow) -> None:
    raise ValueError("the deletion log is append-only")


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
    # The review UI's language this person chose ("de" or "en"); None until
    # they choose, and the browser's language decides.
    language: Mapped[str | None] = mapped_column(String(5))

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


class MailRow(Base):
    """A mail the poller read (app/mailbox.py), and what became of each of
    its parts: an invoice stored, one already known, or refused with a
    reason. A mail with nothing attached is kept too, so nothing sent to the
    mailbox disappears without trace. Sender and subject are data."""

    __tablename__ = "mails"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    mailbox: Mapped[str] = mapped_column(String(400))
    uidvalidity: Mapped[int] = mapped_column(BigInteger)
    uid: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[str] = mapped_column(String(300))
    sender: Mapped[str] = mapped_column(Sealed("mails.sender", "text"))
    subject: Mapped[str] = mapped_column(Sealed("mails.subject", "text"))
    sent: Mapped[str] = mapped_column(Sealed("mails.sent", "text"))  # the Date header, as sent
    # [{"filename", "content_type", "bytes", "outcome", "reason", "document_id"}]
    parts: Mapped[list[dict[str, Any]]] = mapped_column(Sealed("mails.parts"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    __table_args__ = (UniqueConstraint("tenant_id", "mailbox", "uidvalidity", "uid"),)


class MailboxStateRow(Base):
    """How far the poller has read a tenant's mailbox folder (app/mailbox.py):
    IMAP UIDs only grow within one UIDVALIDITY, so the last UID handled is
    enough to never fetch a mail twice. `mailbox` is user@host/folder: another
    mailbox starts afresh."""

    __tablename__ = "mailbox_state"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    mailbox: Mapped[str] = mapped_column(String(400), primary_key=True)
    uidvalidity: Mapped[int] = mapped_column(BigInteger)
    last_uid: Mapped[int] = mapped_column(BigInteger, default=0)
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
    return {"data": invoice.model_dump(mode="json")}


# Databases made from these tables on Postgres get row-level security too;
# migrated ones get it from their migration (app/db/rls.py).
event.listen(Base.metadata, "after_create", rls.install)
