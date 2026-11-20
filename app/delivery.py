"""Delivering decided invoices to downstream systems, exactly once.

An outbox: `deliver_due` (run every few seconds by `python -m app.deliver`)
finds every document that may be delivered and is not yet, and hands it to
each connector. May be delivered: approved by a reviewer, or decided `auto`
by the decision engine — nothing without one of those two on record.

Exactly once rests on two things. Each (document, connector) has one
delivery with one idempotency key, and its booking is stored before the
first attempt, so every attempt sends the same booking under the same key.
And the attempt is claimed — counted, with its next try scheduled — before
anything is sent. A process that dies after the downstream system booked but
before the result was recorded leaves the delivery pending; the next attempt
sends the same key, the downstream system answers with the booking it
already has, and that is recorded.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.connectors.base import Booking, Connector, DeliveryFailed
from app.db.review import ReviewState
from app.db.rls import tenant_transaction
from app.db.tables import DEFAULT_TENANT_ID, AuditEventRow, DeliveryRow, DocumentRow
from app.extract.draft import InvoiceDraft
from app.texts import Text, said

logger = logging.getLogger(__name__)

DELIVERY_ACTOR = "system"
# Attempts before a delivery that keeps failing is given up and shown.
MAX_ATTEMPTS = 8
FIRST_RETRY = timedelta(seconds=30)
LONGEST_WAIT = timedelta(hours=1)

PENDING, DELIVERED, FAILED = "pending", "delivered", "failed"


class NotBookable(ValueError):
    """The document lacks what a booking needs; the message says what."""


def may_deliver(document: DocumentRow) -> bool:
    """Approved by a person, or decided `auto` with that decision on record."""
    if document.review_state == ReviewState.APPROVED.value:
        return True
    return (
        document.review_state == ReviewState.NOT_REQUIRED.value
        and document.decision is not None
        and document.decision.route == "auto"
    )


def booking_for(document: DocumentRow) -> Booking:
    """The invoice as it was approved or decided, with the supplier and the
    order it was matched to."""
    if document.match is None or document.match.supplier_id is None:
        raise NotBookable(Text("delivery.supplier_unknown"))
    invoice = InvoiceDraft.model_validate(document.draft.data).to_invoice()
    if invoice.type_code != "380":
        # A booking has no sign and no document type: a credit note would be
        # booked as an invoice. Until that exists (deferred), it is booked by hand.
        raise NotBookable(Text("delivery.not_an_invoice", type_code=invoice.type_code))

    def text(amount) -> str | None:
        return None if amount is None else str(amount)

    return Booking(
        supplier_id=document.match.supplier_id,
        number=invoice.number,
        issue_date=invoice.issue_date.isoformat(),
        due_date=None if invoice.due_date is None else invoice.due_date.isoformat(),
        currency=invoice.currency,
        net_total=str(invoice.net_total),
        vat_total=text(invoice.vat_total),
        gross_total=str(invoice.gross_total),
        amount_due=str(invoice.amount_due),
        po_number=document.match.po_number,
        iban=invoice.payment_ibans[0] if invoice.payment_ibans else None,
        reference=document.id,
    )


def _channel(connector: Connector) -> str | None:
    """Which way into the downstream system, where a connector has several."""
    return getattr(connector, "channel", None)


def _wait(attempts: int) -> timedelta:
    return min(FIRST_RETRY * 2 ** (attempts - 1), LONGEST_WAIT)


def _event(document: DocumentRow, event: str, **detail) -> None:
    document.audit_events.append(AuditEventRow(event=event, actor=DELIVERY_ACTOR, detail=detail))


@dataclass(frozen=True)
class _Claim:
    delivery_id: str
    booking: Booking
    key: str


def _claim(
    session: Session, document: DocumentRow, connector: Connector, now: datetime
) -> _Claim | None:
    """Start or continue the document's delivery to this connector; None
    when there is nothing to send now."""
    delivery = next((d for d in document.deliveries if d.connector == connector.name), None)
    if delivery is None:
        delivery = DeliveryRow(
            connector=connector.name,
            idempotency_key=f"einvoice:{document.id}:{connector.name}",
            status=PENDING,
            booking={},
            attempts=0,
            next_attempt_at=now,
        )
        try:
            delivery.booking = booking_for(document).as_json()
        except NotBookable as exc:
            delivery.status, delivery.last_error = FAILED, str(exc)
            delivery.error_text = said(exc.args[0] if exc.args else None)
            document.deliveries.append(delivery)
            _event(
                document,
                "delivery_failed",
                connector=connector.name,
                channel=_channel(connector),
                error=str(exc),
            )
            return None
        document.deliveries.append(delivery)
    if delivery.status != PENDING or _aware(delivery.next_attempt_at) > now:
        return None
    delivery.attempts += 1
    delivery.next_attempt_at = now + _wait(delivery.attempts)
    session.flush()
    return _Claim(delivery.id, Booking(**delivery.booking), delivery.idempotency_key)


def _aware(moment: datetime) -> datetime:
    """SQLite gives back naive datetimes for what was stored as UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _record(
    session: Session, claim: _Claim, connector: Connector, now: datetime, *, ref=None, error=None
) -> None:
    delivery = session.get(DeliveryRow, claim.delivery_id, with_for_update=True)
    document = delivery.document
    if error is None:
        delivery.status, delivery.external_ref, delivery.delivered_at = DELIVERED, ref, now
        delivery.last_error = None
        delivery.error_text = None
        _event(
            document,
            "delivered",
            connector=connector.name,
            channel=_channel(connector),
            ref=ref,
            attempt=delivery.attempts,
        )
        return
    message, retryable = str(error), error.retryable
    delivery.last_error = message
    # str() of the exception is plain; its first argument keeps the code.
    delivery.error_text = said(error.args[0] if error.args else None)
    if not retryable or delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = FAILED
    _event(
        document,
        "delivery_failed",
        connector=connector.name,
        channel=_channel(connector),
        error=message,
        attempt=delivery.attempts,
        will_retry=delivery.status == PENDING,
    )


def _candidates(session: Session, connector: Connector, tenant_id: str) -> list[str]:
    """The tenant's documents that may be delivered and are not finished with
    this connector."""
    finished = select(DeliveryRow.document_id).where(
        DeliveryRow.connector == connector.name, DeliveryRow.status != PENDING
    )
    rows = session.scalars(
        select(DocumentRow)
        .where(
            DocumentRow.tenant_id == tenant_id,
            DocumentRow.review_state.in_(
                (ReviewState.APPROVED.value, ReviewState.NOT_REQUIRED.value)
            ),
            DocumentRow.id.not_in(finished),
        )
        .order_by(DocumentRow.created_at, DocumentRow.id)
    )
    return [document.id for document in rows if may_deliver(document)]


def deliver_due(
    session_factory: sessionmaker[Session],
    connectors: Sequence[Connector],
    now: datetime,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> list[tuple[str, str, str]]:
    """Deliver what of the tenant is due to its connectors: (document,
    connector, outcome) for each attempt made. The connectors in .env are the
    default tenant's until tenants have their own settings (v1.0 Stage 2,
    increment 4); another tenant's documents never reach them. The sending
    happens outside any database transaction; each claim and each result is
    committed on its own."""
    done = []
    for connector in connectors:
        with tenant_transaction(session_factory, tenant_id) as session:
            candidates = _candidates(session, connector, tenant_id)
        for document_id in candidates:
            with tenant_transaction(session_factory, tenant_id) as session:
                document = session.get(DocumentRow, document_id, with_for_update=True)
                claim = _claim(session, document, connector, now) if may_deliver(document) else None
            if claim is None:
                continue
            try:
                ref = connector.deliver(claim.booking, claim.key)
            except DeliveryFailed as exc:
                logger.warning("document %s to %s: %s", document_id, connector.name, exc)
                with tenant_transaction(session_factory, tenant_id) as session:
                    _record(session, claim, connector, now, error=exc)
                done.append((document_id, connector.name, "failed"))
                continue
            with tenant_transaction(session_factory, tenant_id) as session:
                _record(session, claim, connector, now, ref=ref)
            done.append((document_id, connector.name, "delivered"))
    return done
