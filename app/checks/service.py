"""Running the checks on a stored document.

This is where the lookups happen that the rules themselves do not make: the
supplier and the order from the ERP, the other invoices on file from the
database. The findings replace the document's previous ones.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checks.context import CheckContext, CheckSettings, KnownInvoice, MasterData
from app.checks.finding import Finding, Severity
from app.checks.rules import run_checks
from app.db.review import ReviewState
from app.db.tables import AuditEventRow, DocumentRow, DraftRow, FindingRow
from app.erp.client import ErpClient, ErpUnavailable
from app.extract.draft import InvoiceDraft
from app.pipeline import DocumentStatus

logger = logging.getLogger(__name__)

CHECKS_ACTOR = "system"


def _known_invoices(session: Session, document: DocumentRow) -> tuple[KnownInvoice, ...]:
    """Every other document's invoice, as far as duplicate detection needs it.
    Reads all drafts; fine for thousands of documents, not for millions."""
    known = []
    rows = session.execute(
        select(DraftRow.document_id, DraftRow.data).where(DraftRow.document_id != document.id)
    )
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


@dataclass
class Checker:
    """Checks documents against the ERP it was given, or without one."""

    erp: ErpClient | None = None
    settings: CheckSettings = field(default_factory=CheckSettings)

    def context(self, session: Session, document: DocumentRow, draft: InvoiceDraft) -> CheckContext:
        known = _known_invoices(session, document)
        if self.erp is None:
            return CheckContext(
                master_data=MasterData.NOT_CONFIGURED, known_invoices=known, settings=self.settings
            )
        try:
            supplier = self.erp.find_supplier(draft.seller.vat_id, draft.seller.name)
            order = None if draft.po_ref is None else self.erp.get_purchase_order(draft.po_ref)
        except ErpUnavailable as exc:
            logger.warning("document %s: ERP not available for the checks: %s", document.id, exc)
            return CheckContext(
                master_data=MasterData.UNAVAILABLE,
                master_data_problem=str(exc),
                known_invoices=known,
                settings=self.settings,
            )
        return CheckContext(
            supplier=supplier, purchase_order=order, known_invoices=known, settings=self.settings
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
        findings = run_checks(draft, self.context(session, document, draft))
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
