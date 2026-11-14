"""Matching and checking a stored document.

This is where the lookups happen that the rules themselves do not make: the
supplier, its orders and the transfers to it from the ERP, the other invoices
on file from the database. The invoice is matched first, so the checks compare
it with the order that was found, not only with one it quotes letter for
letter. Match and findings replace the document's previous ones.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checks.context import (
    CheckContext,
    CheckSettings,
    KnownInvoice,
    MasterData,
    PoLine,
    PurchaseOrder,
)
from app.checks.finding import Finding, Severity
from app.checks.rules import run_checks
from app.db.review import ReviewState
from app.db.tables import AuditEventRow, DocumentRow, DraftRow, FindingRow, MatchRow
from app.erp.client import ErpUnavailable
from app.extract.draft import InvoiceDraft
from app.matching import Basis, Match, MatchingData, PeerInvoice, match_candidates
from app.pipeline import DocumentStatus

logger = logging.getLogger(__name__)

CHECKS_ACTOR = "system"


def _known_invoices(session: Session, document: DocumentRow) -> tuple[KnownInvoice, ...]:
    """Every other document's invoice, as far as duplicate detection needs it.
    Reads all drafts; fine for thousands of documents, not for millions."""
    rows = session.execute(
        select(DraftRow.document_id, DraftRow.data).where(DraftRow.document_id != document.id)
    )
    return _as_known(rows)


def _billed_before(
    session: Session, document: DocumentRow, match: Match
) -> tuple[KnownInvoice, ...]:
    """The other documents matched to the delivery this one was matched to."""
    if match.delivery_id is None:
        return ()
    rows = session.execute(
        select(DraftRow.document_id, DraftRow.data)
        .join(MatchRow, MatchRow.document_id == DraftRow.document_id)
        .where(MatchRow.delivery_id == match.delivery_id, MatchRow.document_id != document.id)
        .order_by(DraftRow.document_id)
    )
    return _as_known(rows)


def _as_known(rows) -> tuple[KnownInvoice, ...]:
    known = []
    for document_id, data in rows:
        seller = data.get("seller") or {}
        try:
            gross = None if data.get("gross_total") is None else Decimal(data["gross_total"])
            issued = (
                None if data.get("issue_date") is None else date.fromisoformat(data["issue_date"])
            )
        except (InvalidOperation, ValueError):
            logger.warning("draft of document %s has an unreadable amount or date", document_id)
            continue
        known.append(
            KnownInvoice(
                document_id=document_id,
                seller_name=seller.get("name"),
                seller_vat_id=seller.get("vat_id"),
                number=data.get("number"),
                issue_date=issued,
                gross_total=gross,
            )
        )
    return tuple(known)


def _same_seller(draft: InvoiceDraft, known: KnownInvoice) -> bool:
    if draft.seller.vat_id and known.seller_vat_id:
        return draft.seller.vat_id == known.seller_vat_id
    return bool(draft.seller.name) and draft.seller.name == known.seller_name


def _order_note(draft: InvoiceDraft, match: Match) -> str | None:
    """How the order was found, when the invoice does not quote it as it is."""
    if match.po_number is None or match.po_basis is Basis.EXACT_REFERENCE:
        return None
    said = f"names {draft.po_ref}" if draft.po_ref else "names no purchase order"
    how = (
        "which differs from it by a slip; a delivery confirms it"
        if match.po_basis is Basis.SIMILAR_REFERENCE
        else "found by the delivered items"
    )
    return f"the invoice {said}; matched to order {match.po_number}, {how}"


def _store_match(session: Session, document: DocumentRow, match: Match | None) -> None:
    """Replace the document's match. The old row goes first: there is one
    match per document, and the database enforces it."""
    if document.match is not None:
        session.delete(document.match)
        document.match = None
        session.flush()
    if match is None:
        return
    document.match = MatchRow(
        supplier_id=match.supplier_id,
        po_number=match.po_number,
        po_basis=match.po_basis.value,
        delivery_id=match.delivery_id,
        payments=[
            {"bank_transaction_id": p.bank_transaction_id, "amount": str(p.amount)}
            for p in match.payments
        ],
        payment_basis=match.payment_basis.value,
        explanation=list(match.explanation),
    )


@dataclass
class Checker:
    """Matches documents to the ERP's data and checks them, or checks what
    can be checked without an ERP."""

    erp: MatchingData | None = None
    settings: CheckSettings = field(default_factory=CheckSettings)

    def context(
        self, session: Session, document: DocumentRow, draft: InvoiceDraft
    ) -> tuple[CheckContext, Match | None]:
        """What the checks need, and the match it is based on (None when the
        ERP could not be asked)."""
        known = _known_invoices(session, document)
        if self.erp is None:
            return (
                CheckContext(
                    master_data=MasterData.NOT_CONFIGURED,
                    known_invoices=known,
                    settings=self.settings,
                ),
                None,
            )
        try:
            supplier = self.erp.find_supplier(draft.seller.vat_id, draft.seller.name)
            if supplier is None:
                match = Match(explanation=("the supplier is not on file, so nothing is matched",))
                orders = []
            else:
                orders = self.erp.orders_of(supplier.id)
                peers = [
                    PeerInvoice(invoice.number, invoice.gross_total)
                    for invoice in known
                    if invoice.number and invoice.gross_total is not None
                    if _same_seller(draft, invoice)
                ]
                match = match_candidates(
                    draft, supplier, orders, self.erp.transactions_to(supplier.ibans), peers
                )
        except ErpUnavailable as exc:
            logger.warning("document %s: ERP not available for the checks: %s", document.id, exc)
            return (
                CheckContext(
                    master_data=MasterData.UNAVAILABLE,
                    master_data_problem=str(exc),
                    known_invoices=known,
                    settings=self.settings,
                ),
                None,
            )
        order = next((order for order in orders if order.number == match.po_number), None)
        undecided = match.po_basis is Basis.AMBIGUOUS
        return (
            CheckContext(
                supplier=supplier,
                purchase_order=None
                if order is None
                else PurchaseOrder(
                    order.number,
                    tuple(
                        PoLine(line.description, line.quantity, line.unit_price)
                        for line in order.lines
                    ),
                ),
                purchase_order_note=_order_note(draft, match),
                purchase_order_problem=match.explanation[0] if undecided else None,
                known_invoices=known,
                delivery_id=match.delivery_id,
                delivery_billed_by=_billed_before(session, document, match),
                settings=self.settings,
            ),
            match,
        )

    def check(self, session: Session, document: DocumentRow) -> list[Finding]:
        """Run the checks on the document's current draft and store the result.

        A finding that needs a person puts the document into review, even a
        structured invoice the validator accepted. An approved document is
        left alone: its findings are what the reviewer saw.
        """
        if document.draft is None or document.review_state == ReviewState.APPROVED.value:
            return []
        draft = InvoiceDraft.model_validate(document.draft.data)
        context, match = self.context(session, document, draft)
        _store_match(session, document, match)
        findings = run_checks(draft, context)
        document.findings = [
            FindingRow(
                position=position,
                type=finding.type.value,
                severity=finding.severity.value,
                message=finding.message,
                fields=list(finding.fields),
                detail=finding.detail,
            )
            for position, finding in enumerate(findings)
        ]
        needs_person = any(finding.severity is Severity.REVIEW for finding in findings)
        if needs_person:
            document.review_state = ReviewState.PENDING.value
        elif (
            document.status == DocumentStatus.VALID.value
            and document.draft.data == document.draft.original
        ):
            # A valid e-invoice, exactly as it arrived, with nothing left to
            # look at. One that a reviewer changed stays in review until it is
            # approved: a correction must not make a finding and the review
            # disappear together.
            document.review_state = ReviewState.NOT_REQUIRED.value
        document.audit_events.append(
            AuditEventRow(
                event="checked",
                actor=CHECKS_ACTOR,
                detail={
                    "findings": [finding.type.value for finding in findings],
                    "needs_review": needs_person,
                    "matched_order": None if match is None else match.po_number,
                    "matched_payments": None
                    if match is None
                    else [p.bank_transaction_id for p in match.payments],
                },
            )
        )
        session.flush()
        return findings


def build_checker(erp_url: str | None, timeout_seconds: float) -> Checker:
    """The checker for an installation, from its settings."""
    if erp_url is None:
        return Checker()
    import httpx

    from app.erp.client import HttpErpClient

    return Checker(erp=HttpErpClient(httpx.Client(base_url=erp_url, timeout=timeout_seconds)))
