"""Matching, checking and deciding on a stored document.

This is where the lookups happen that the rules themselves do not make: the
supplier, its orders and the transfers to it from the ERP, the other invoices
on file from the database. The invoice is matched first, so the checks compare
it with the order that was found, not only with one it quotes letter for
letter. Match and findings replace the document's previous ones.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
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
from app.checks.finding import Finding
from app.checks.rules import run_checks
from app.db.review import ReviewState
from app.db.tables import (
    AuditEventRow,
    DecisionRow,
    DocumentRow,
    DraftRow,
    FindingRow,
    MatchRow,
)
from app.decide import Decision, DecisionSettings, Route, decide
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


def _store_decision(document: DocumentRow, decision: Decision) -> None:
    """Replace the document's decision (one per document, like the match)."""
    reasons = [
        {
            "cause": reason.cause.value,
            "message": reason.message,
            "fields": list(reason.fields),
            "findings": list(reason.findings),
        }
        for reason in decision.reasons
    ]
    if document.decision is None:
        document.decision = DecisionRow(
            route=decision.route.value, reasons=reasons, version=decision.version
        )
    else:
        document.decision.route = decision.route.value
        document.decision.reasons = reasons
        document.decision.version = decision.version
        document.decision.decided_at = datetime.now(UTC)


@dataclass
class Checker:
    """Matches documents to the ERP's data and checks them, or checks what
    can be checked without an ERP."""

    erp: MatchingData | None = None
    settings: CheckSettings = field(default_factory=CheckSettings)
    decisions: DecisionSettings = field(default_factory=DecisionSettings.load)

    def context(
        self,
        session: Session,
        document: DocumentRow,
        draft: InvoiceDraft,
        *,
        erp_must_answer: bool = False,
    ) -> tuple[CheckContext, Match | None]:
        """What the checks need, and the match it is based on (None when the
        ERP could not be asked). With `erp_must_answer`, an ERP that does not
        answer raises ErpUnavailable instead of being noted."""
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
            if erp_must_answer:
                raise
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

    def check(
        self,
        session: Session,
        document: DocumentRow,
        *,
        trigger: str | None = None,
        erp_must_answer: bool = False,
    ) -> list[Finding]:
        """Match and check the document's current draft, decide whether it
        may be booked without a person (app/decide), and store all three.

        The decision sets the review state: an extracted PDF read with
        confidence can skip review, a structured invoice the validator
        accepted can still need it. An approved document is left alone: its
        findings are what the reviewer saw (see `match_payments`).

        `trigger` says why it runs again, for the audit trail; a run that
        `erp_must_answer` raises ErpUnavailable and changes nothing when the
        ERP does not answer, instead of noting the outage on the document.
        """
        if document.draft is None or document.review_state == ReviewState.APPROVED.value:
            return []
        draft = InvoiceDraft.model_validate(document.draft.data)
        context, match = self.context(session, document, draft, erp_must_answer=erp_must_answer)
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
        decision = decide(
            draft,
            status=DocumentStatus(document.status),
            findings=findings,
            match=match,
            # A correction must not make a finding and the review disappear
            # together: a changed draft waits for a person's approval.
            corrected=document.draft.data != document.draft.original,
            settings=self.decisions,
        )
        _store_decision(document, decision)
        document.review_state = (
            ReviewState.NOT_REQUIRED if decision.route is Route.AUTO else ReviewState.PENDING
        ).value
        document.audit_events.append(
            AuditEventRow(
                event="checked",
                actor=CHECKS_ACTOR,
                detail={
                    "findings": [finding.type.value for finding in findings],
                    "route": decision.route.value,
                    "reasons": [reason.cause.value for reason in decision.reasons],
                    "thresholds": decision.version,
                    "matched_order": None if match is None else match.po_number,
                    "matched_payments": None
                    if match is None
                    else [p.bank_transaction_id for p in match.payments],
                }
                | ({} if trigger is None else {"trigger": trigger}),
            )
        )
        session.flush()
        return findings

    def match_payments(self, session: Session, document: DocumentRow, *, trigger: str) -> bool:
        """Look for the payments of an approved invoice again, and nothing
        else: order, delivery, findings and decision stay what the reviewer
        approved. Raises ErpUnavailable if the ERP does not answer. Returns
        whether the payments changed."""
        if self.erp is None or document.match is None:
            return False
        draft = InvoiceDraft.model_validate(document.draft.data)
        context, match = self.context(session, document, draft, erp_must_answer=True)
        if match is None:
            return False
        stored = document.match
        before = [p["bank_transaction_id"] for p in stored.payments]
        after = [p.bank_transaction_id for p in match.payments]
        if (before, stored.payment_basis) == (after, match.payment_basis.value):
            return False
        stored.payments = [
            {"bank_transaction_id": p.bank_transaction_id, "amount": str(p.amount)}
            for p in match.payments
        ]
        stored.payment_basis = match.payment_basis.value
        if (match.po_number, match.delivery_id) == (stored.po_number, stored.delivery_id):
            stored.explanation = list(match.explanation)
        else:
            stored.explanation = [
                *stored.explanation,
                f"payments matched again; order {stored.po_number} and delivery "
                f"{stored.delivery_id} stay as approved",
            ]
        document.audit_events.append(
            AuditEventRow(
                event="payments_matched",
                actor=CHECKS_ACTOR,
                detail={"trigger": trigger, "before": before, "after": after},
            )
        )
        session.flush()
        return True


def build_checker(erp_url: str | None, timeout_seconds: float) -> Checker:
    """The checker for an installation, from its settings."""
    if erp_url is None:
        return Checker()
    import httpx

    from app.erp.client import HttpErpClient

    return Checker(erp=HttpErpClient(httpx.Client(base_url=erp_url, timeout=timeout_seconds)))
