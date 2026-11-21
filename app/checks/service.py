"""Matching, checking and deciding on a stored document.

This is where the lookups happen that the rules themselves do not make: the
supplier, its orders and the transfers to it from the ERP, the other invoices
on file from the database. The invoice is matched first, so the checks compare
it with the order that was found, not only with one it quotes letter for
letter. Match and findings replace the document's previous ones.
"""

import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import literal, select, tuple_
from sqlalchemy.orm import Session

from app.checks.context import (
    CheckContext,
    CheckSettings,
    KnownInvoice,
    KnownLine,
    MasterData,
    PoLine,
    PurchaseOrder,
)
from app.checks.credit import CreditLink, link_credit
from app.checks.finding import Finding
from app.checks.rules import run_checks
from app.db.review import ReviewState
from app.db.tables import (
    AuditEventRow,
    CreditLinkRow,
    DecisionRow,
    DocumentRow,
    DraftRow,
    FindingRow,
    MatchRow,
)
from app.decide import Decision, DecisionSettings, Route, decide
from app.erp.client import ErpUnavailable
from app.extract.draft import InvoiceDraft
from app.extract.ollama import is_local
from app.matching import (
    Basis,
    Match,
    MatchingData,
    PeerInvoice,
    match_candidates,
    match_credit_payments,
)
from app.models.invoice import CREDIT_NOTE
from app.pipeline import DocumentStatus
from app.settings import Settings
from app.texts import Text, said

_DECIDED_BY_A_PERSON = frozenset({ReviewState.APPROVED.value, ReviewState.REJECTED.value})

logger = logging.getLogger(__name__)

CHECKS_ACTOR = "system"


def _received_before(document: DocumentRow):
    """Documents received before this one. Only an earlier invoice makes this
    one a duplicate: checked again when a copy arrives, the original must not
    become a copy of its own copy."""
    return tuple_(DocumentRow.created_at, DocumentRow.id) < tuple_(
        literal(document.created_at), literal(document.id)
    )


def _known_invoices(
    session: Session, document: DocumentRow, *, earlier_only: bool = False
) -> tuple[KnownInvoice, ...]:
    """Every other document's invoice of the same tenant: all of them for
    payment matching (a combined transfer pays later invoices too), only the
    earlier ones for duplicate detection. Reads all drafts; fine for thousands
    of documents, not for millions."""
    query = (
        select(DraftRow.document_id, DraftRow.data, DocumentRow.review_state)
        .join(DocumentRow, DocumentRow.id == DraftRow.document_id)
        .where(DocumentRow.tenant_id == document.tenant_id, DraftRow.document_id != document.id)
    )
    if earlier_only:
        query = query.where(_received_before(document))
    return _as_known(session.execute(query))


def _billed_before(
    session: Session, document: DocumentRow, match: Match
) -> tuple[KnownInvoice, ...]:
    """The earlier documents matched to the delivery this one was matched to."""
    if match.delivery_id is None:
        return ()
    rows = session.execute(
        select(DraftRow.document_id, DraftRow.data, DocumentRow.review_state)
        .join(MatchRow, MatchRow.document_id == DraftRow.document_id)
        .join(DocumentRow, DocumentRow.id == DraftRow.document_id)
        .where(
            DocumentRow.tenant_id == document.tenant_id,
            MatchRow.delivery_id == match.delivery_id,
            MatchRow.document_id != document.id,
            _received_before(document),
        )
        .order_by(DraftRow.document_id)
    )
    return _as_known(rows)


def _known_lines(data: dict) -> tuple[KnownLine, ...]:
    lines = []
    for line in data.get("lines") or []:
        try:
            if line.get("description") and line.get("unit_price") is not None:
                quantity = Decimal(line["quantity"]) if line.get("quantity") is not None else None
                lines.append(
                    KnownLine(
                        description=line["description"],
                        quantity=quantity if quantity is not None else Decimal(0),
                        unit_price=Decimal(line["unit_price"]),
                    )
                )
        except (InvalidOperation, TypeError):
            continue
    return tuple(lines)


def _as_known(rows) -> tuple[KnownInvoice, ...]:
    known = []
    for document_id, data, review_state in rows:
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
                type_code=data.get("type_code"),
                rejected=review_state == ReviewState.REJECTED.value,
                lines=_known_lines(data),
            )
        )
    return tuple(known)


def _credited_before(session: Session, document: DocumentRow, invoice_document_id: str) -> Decimal:
    """What the other credit notes linked to the invoice took back: those
    received before this one and not rejected. A total that cannot be read
    counts as nothing (it is flagged on its own document)."""
    totals = session.execute(
        select(DraftRow.data["gross_total"])
        .join(CreditLinkRow, CreditLinkRow.document_id == DraftRow.document_id)
        .join(DocumentRow, DocumentRow.id == DraftRow.document_id)
        .where(
            CreditLinkRow.invoice_document_id == invoice_document_id,
            DocumentRow.tenant_id == document.tenant_id,
            DocumentRow.id != document.id,
            DocumentRow.review_state != ReviewState.REJECTED.value,
            _received_before(document),
        )
    ).scalars()
    credited = Decimal(0)
    for total in totals:
        try:
            credited += Decimal(str(total)) if total is not None else Decimal(0)
        except InvalidOperation:
            continue
    return credited


def _store_credit_link(document: DocumentRow, link: CreditLink | None) -> None:
    """Replace the document's link; none for anything but a credit note."""
    if link is None:
        document.credit_link = None
        return
    row = CreditLinkRow(
        invoice_document_id=None if link.invoice is None else link.invoice.document_id,
        basis=link.basis.value,
        explanation=str(link.explanation),
        explanation_text=said(link.explanation),
    )
    if document.credit_link is None:
        document.credit_link = row
    else:
        for name in ("invoice_document_id", "basis", "explanation", "explanation_text"):
            setattr(document.credit_link, name, getattr(row, name))


def _peers(draft: InvoiceDraft, known: tuple[KnownInvoice, ...]) -> list[PeerInvoice]:
    """The supplier's other documents on file, for telling whose a transfer
    is; its credit notes marked, and rejected documents left out (they are
    neither paid nor offset)."""
    return [
        PeerInvoice(invoice.number, invoice.gross_total, credit=invoice.type_code == CREDIT_NOTE)
        for invoice in known
        if invoice.number and invoice.gross_total is not None and not invoice.rejected
        if _same_seller(draft, invoice)
    ]


def _same_seller(draft: InvoiceDraft, known: KnownInvoice) -> bool:
    if draft.seller.vat_id and known.seller_vat_id:
        return draft.seller.vat_id == known.seller_vat_id
    return bool(draft.seller.name) and draft.seller.name == known.seller_name


def _order_note(draft: InvoiceDraft, match: Match) -> Text | None:
    """How the order was found, when the invoice does not quote it as it is."""
    if match.po_number is None or match.po_basis is Basis.EXACT_REFERENCE:
        return None
    if match.po_basis is Basis.SIMILAR_REFERENCE:
        return Text("order_note.slip", po_ref=draft.po_ref, order=match.po_number)
    if draft.po_ref:
        return Text("order_note.content", po_ref=draft.po_ref, order=match.po_number)
    return Text("order_note.content_unnamed", order=match.po_number)


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
        payments=_payments_json(match),
        payment_basis=match.payment_basis.value,
        explanation=list(match.explanation),
        explanation_texts=[said(line) for line in match.explanation],
    )


def _payments_json(match: Match) -> list[dict]:
    """A part settled by a credit note says which (v1.0 Stage 5)."""
    return [
        {"bank_transaction_id": p.bank_transaction_id, "amount": str(p.amount)}
        | ({} if p.credit_note is None else {"credit_note": p.credit_note})
        for p in match.payments
    ]


def _store_decision(document: DocumentRow, decision: Decision) -> None:
    """Replace the document's decision (one per document, like the match)."""
    reasons = [
        {
            "cause": reason.cause.value,
            "message": reason.message,
            "text": said(reason.message),
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
    decisions: DecisionSettings = field(default_factory=lambda: installation_decisions())

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
        earlier = _known_invoices(session, document, earlier_only=True)
        credit = link_credit(draft, known)
        if credit is not None and credit.invoice is not None:
            credit = replace(
                credit,
                credited_before=_credited_before(session, document, credit.invoice.document_id),
            )
        if self.erp is None:
            return (
                CheckContext(
                    master_data=MasterData.NOT_CONFIGURED,
                    known_invoices=earlier,
                    credit=credit,
                    settings=self.settings,
                ),
                None,
            )
        try:
            supplier = self.erp.find_supplier(draft.seller.vat_id, draft.seller.name)
            if supplier is None:
                match = Match(explanation=(Text("match.no_supplier"),))
                orders = []
            elif credit is not None:
                # A credit note answers to its invoice: no order or delivery of
                # its own. It is settled by a refund or an offset.
                payments, basis, notes = match_credit_payments(
                    draft, self.erp.transactions_to(supplier.ibans), _peers(draft, known)
                )
                match = Match(
                    supplier_id=supplier.id,
                    payments=payments,
                    payment_basis=basis,
                    explanation=(credit.explanation, *notes),
                )
                orders = []
            else:
                orders = self.erp.orders_of(supplier.id)
                peers = _peers(draft, known)
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
                    known_invoices=earlier,
                    credit=credit,
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
                known_invoices=earlier,
                delivery_id=match.delivery_id,
                delivery_billed_by=_billed_before(session, document, match),
                credit=credit,
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
        accepted can still need it. A document a person approved or rejected
        is left alone: its findings are what the reviewer saw (see
        `match_payments`).

        `trigger` says why it runs again, for the audit trail; a run that
        `erp_must_answer` raises ErpUnavailable and changes nothing when the
        ERP does not answer, instead of noting the outage on the document.
        """
        if document.draft is None or document.review_state in _DECIDED_BY_A_PERSON:
            return []
        draft = InvoiceDraft.model_validate(document.draft.data)
        context, match = self.context(session, document, draft, erp_must_answer=erp_must_answer)
        _store_match(session, document, match)
        _store_credit_link(document, context.credit)
        findings = run_checks(draft, context)
        document.findings = [
            FindingRow(
                position=position,
                type=finding.type.value,
                severity=finding.severity.value,
                message=finding.message,
                text=said(finding.message),
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
                | ({} if trigger is None else {"trigger": trigger, "trigger_text": said(trigger)}),
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
        stored.payments = _payments_json(match)
        stored.payment_basis = match.payment_basis.value
        if (match.po_number, match.delivery_id) == (stored.po_number, stored.delivery_id):
            stored.explanation = list(match.explanation)
            stored.explanation_texts = [said(line) for line in match.explanation]
        else:
            note = Text("match.payments_again", order=stored.po_number, delivery=stored.delivery_id)
            texts = stored.explanation_texts or [None] * len(stored.explanation)
            stored.explanation = [*stored.explanation, note]
            stored.explanation_texts = [*texts, said(note)]
        document.audit_events.append(
            AuditEventRow(
                event="payments_matched",
                actor=CHECKS_ACTOR,
                detail={
                    "trigger": trigger,
                    "trigger_text": said(trigger),
                    "before": before,
                    "after": after,
                },
            )
        )
        session.flush()
        return True


def installation_decisions() -> DecisionSettings:
    """The fitted thresholds, and whether this installation reads PDFs and
    scans with a model they were not calibrated on (a local one). Every
    checker takes these: worker, API and bank watch alike."""
    decisions = DecisionSettings.load()
    model = Settings.from_env().extractor_model
    return replace(decisions, uncalibrated_reader=model) if is_local(model) else decisions


def build_checker(erp_url: str | None, timeout_seconds: float) -> Checker:
    """The checker for an installation, from its settings."""
    if erp_url is None:
        return Checker()
    import httpx

    from app.erp.client import HttpErpClient

    return Checker(erp=HttpErpClient(httpx.Client(base_url=erp_url, timeout=timeout_seconds)))
