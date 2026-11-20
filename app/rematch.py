"""Matching again when new data arrives.

An invoice is matched once, when it is processed, but what it is matched to
keeps arriving: the payment comes weeks later, and a transfer that settles
three invoices is only recognised once all three are on file. So a
supplier's invoices that are not fully paid yet are matched again when

- another invoice of that supplier has been processed (`rematch_supplier`,
  called by the processing job), or
- a new transfer to one of the supplier's accounts appears in the ERP
  (`watch_bank`, run every few minutes by `python -m app.watch`; the ERP
  cannot tell us, so the watch asks for what it has not seen yet).

An invoice not yet approved is matched, checked and decided again in full.
An approved one only has its payments looked for again: order, delivery,
findings and decision stay what the reviewer approved.

A re-match never makes things worse when the ERP does not answer: the
invoice keeps its match, and the outage is logged (supplier) or raised so
the next watch tries again (bank).
"""

import logging
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.checks.context import Supplier
from app.checks.service import Checker
from app.db.review import ReviewState
from app.db.tables import DocumentRow, MatchRow, SeenBankTransactionRow
from app.erp.client import ErpUnavailable
from app.matching import BankTransaction, Basis

logger = logging.getLogger(__name__)

# Bank statements are booked late and out of order: the watch looks back this
# far behind the latest transaction it has seen.
LOOK_BACK = timedelta(days=31)
_NAMED = {Basis.EXACT_REFERENCE.value, Basis.SIMILAR_REFERENCE.value}


class BankData(Protocol):
    """What the bank watch reads from the ERP (app/erp/client.py)."""

    def transactions_since(self, since: date | None) -> list[BankTransaction]: ...

    def supplier_with_iban(self, iban: str) -> Supplier | None: ...


def fully_paid(document: DocumentRow) -> bool:
    """Whether the matched payments cover what the invoice asks for, on
    transfers that name it. A payment found by its amount alone is
    provisional: another invoice of the same amount may not be on file yet,
    and the transfer that quotes this one may still come."""
    if document.match is None or document.draft is None:
        return False
    if document.match.payment_basis not in _NAMED:
        return False
    data = document.draft.data
    owed = data.get("amount_due") or data.get("gross_total")
    try:
        paid = sum((Decimal(p["amount"]) for p in document.match.payments), Decimal(0))
        return owed is not None and paid >= Decimal(owed)
    except (InvalidOperation, KeyError, TypeError):
        logger.warning("document %s: unreadable amount or payment", document.id)
        return False


def open_invoices(
    session: Session, tenant_id: str, supplier_id: str, exclude: str | None = None
) -> list[DocumentRow]:
    """The tenant's documents of the supplier whose payments are not all
    matched yet. Supplier ids come from the tenant's own ERP."""
    query = (
        select(DocumentRow)
        .join(MatchRow, MatchRow.document_id == DocumentRow.id)
        .where(DocumentRow.tenant_id == tenant_id, MatchRow.supplier_id == supplier_id)
        # A rejected invoice is not owed, so no transfer pays it.
        .where(DocumentRow.review_state != ReviewState.REJECTED.value)
        .order_by(DocumentRow.created_at, DocumentRow.id)
    )
    if exclude is not None:
        query = query.where(DocumentRow.id != exclude)
    return [document for document in session.scalars(query) if not fully_paid(document)]


def rematch(session: Session, checker: Checker, document: DocumentRow, trigger: str) -> None:
    """Match one document again. Raises ErpUnavailable, leaving it unchanged,
    if the ERP does not answer."""
    if document.review_state == ReviewState.REJECTED.value:
        return
    if document.review_state == ReviewState.APPROVED.value:
        checker.match_payments(session, document, trigger=trigger)
    else:
        checker.check(session, document, trigger=trigger, erp_must_answer=True)


def rematch_supplier(
    session: Session,
    checker: Checker,
    tenant_id: str,
    supplier_id: str,
    trigger: str,
    exclude: str | None = None,
) -> list[str]:
    """Match the tenant's open invoices of the supplier again; the ids of
    those it did."""
    done = []
    for document in open_invoices(session, tenant_id, supplier_id, exclude):
        rematch(session, checker, document, trigger)
        done.append(document.id)
    return done


def after_processing(session: Session, checker: Checker, document: DocumentRow) -> list[str]:
    """Called once a new document is matched: the same supplier's open
    invoices may now be settled together with it. An ERP that stops
    answering half-way leaves the rest as they were, and does not fail the
    new document."""
    if document.match is None or document.match.supplier_id is None:
        return []
    try:
        return rematch_supplier(
            session,
            checker,
            document.tenant_id,
            document.match.supplier_id,
            trigger=f"new invoice {document.draft.data.get('number')} of the supplier",
            exclude=document.id,
        )
    except ErpUnavailable as exc:
        logger.warning(
            "supplier %s: invoices not matched again, ERP not available: %s",
            document.match.supplier_id,
            exc,
        )
        return []


def watch_bank(session: Session, erp: BankData, checker: Checker, tenant_id: str) -> list[str]:
    """Act on the transfers in the tenant's ERP that are new since the last
    watch; their ids.

    The first watch sees every transaction as new and matches every open
    invoice of a supplier that was paid anything once. Raises ErpUnavailable
    when the ERP does not answer: nothing is recorded as seen, so the next
    watch tries the same transfers again.
    """
    latest = _latest_seen(session, tenant_id)
    since = None if latest is None else latest - LOOK_BACK
    transactions = erp.transactions_since(since)
    seen_query = select(SeenBankTransactionRow.id).where(
        SeenBankTransactionRow.tenant_id == tenant_id
    )
    if since is not None:
        seen_query = seen_query.where(SeenBankTransactionRow.booking_date >= since)
    seen = set(session.scalars(seen_query))
    new = sorted((tx for tx in transactions if tx.id not in seen), key=lambda tx: tx.id)

    by_supplier: dict[str, list[str]] = {}
    owners: dict[str, Supplier | None] = {}
    for tx in new:
        if tx.counterparty_iban not in owners:
            owners[tx.counterparty_iban] = erp.supplier_with_iban(tx.counterparty_iban)
        owner = owners[tx.counterparty_iban]
        if owner is not None:  # rent, salaries, tax: no supplier, nothing to match
            by_supplier.setdefault(owner.id, []).append(tx.id)
    for supplier_id, ids in sorted(by_supplier.items()):
        rematch_supplier(session, checker, tenant_id, supplier_id, trigger=_trigger(ids))

    session.add_all(
        SeenBankTransactionRow(tenant_id=tenant_id, id=tx.id, booking_date=tx.booking_date)
        for tx in new
    )
    session.flush()
    _forget_old(session, tenant_id)
    if new:
        logger.info(
            "bank watch: %d new transactions, %d suppliers matched again",
            len(new),
            len(by_supplier),
        )
    return [tx.id for tx in new]


def _trigger(ids: Sequence[str]) -> str:
    shown = ", ".join(ids[:5]) + (f" and {len(ids) - 5} more" if len(ids) > 5 else "")
    return f"new bank transaction{'s' if len(ids) > 1 else ''} {shown}"


def _latest_seen(session: Session, tenant_id: str):
    return session.scalar(
        select(func.max(SeenBankTransactionRow.booking_date)).where(
            SeenBankTransactionRow.tenant_id == tenant_id
        )
    )


def _forget_old(session: Session, tenant_id: str) -> None:
    """Keep only what the tenant's next watch's look-back can still return."""
    latest = _latest_seen(session, tenant_id)
    if latest is not None:
        session.execute(
            delete(SeenBankTransactionRow).where(
                SeenBankTransactionRow.tenant_id == tenant_id,
                SeenBankTransactionRow.booking_date < latest - LOOK_BACK,
            )
        )
