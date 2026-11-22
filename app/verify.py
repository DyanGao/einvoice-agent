"""Tamper evidence (v1.0 Stage 6, increment 4).

    einvoice verify             every original against its SHA-256, every history
                                against its hash chain (app/db/chain.py); exit 1 on a break
    einvoice chain [--apply]    chain the events stored before the chain existed

What a break says:

- original_changed: the original no longer has the SHA-256 recorded when it
  arrived (or does not open at all: original_unreadable);
- event_changed: an event says something else than when it was chained;
- chain_broken: the hash before an event is not the event before it: one was
  removed, put in between, or changed together with its own hash;
- event_unchained: an event without a hash after chained ones: added past
  the chain;
- history_cut: the history ends before the newest hash the document
  recorded: events at its end were removed;
- event_unreadable: an event's sealed detail does not open.

What it cannot say: someone who can write to the database can also make a
whole new, consistent chain. Writing the result of `einvoice verify` (its
digest) down outside the database, or the audit export (increment 6), is
what shows that later.
"""

import hashlib
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import crypto
from app.db.chain import event_hash
from app.db.rls import tenant_transaction
from app.db.tables import AuditEventRow, DocumentRow, TenantRow


@dataclass(frozen=True)
class Problem:
    tenant: str
    document_id: str
    kind: str
    seq: int | None = None

    def __str__(self) -> str:
        where = "" if self.seq is None else f" (event {self.seq})"
        return f"{self.tenant} {self.document_id}: {self.kind}{where}"


@dataclass
class Report:
    documents: int = 0
    events: int = 0
    unchained: int = 0  # events from before the chain, not chained yet
    problems: list[Problem] = field(default_factory=list)
    # A digest of every history's newest hash: written down outside the
    # database, it shows later whether anything before it was changed.
    digest: str = ""


def _problems(document: DocumentRow, tenant: str) -> tuple[list[Problem], int, int]:
    """The document's breaks, its events and how many are not chained yet."""
    found: list[Problem] = []

    def problem(kind: str, seq: int | None = None) -> None:
        found.append(Problem(tenant, document.id, kind, seq))

    try:
        if hashlib.sha256(document.content).hexdigest() != document.sha256:
            problem("original_changed")
    except crypto.CryptoError:
        problem("original_unreadable")

    before, chained, unchained = document.sha256, False, 0
    events = list(document.audit_events)
    for row in events:
        if row.hash is None:
            if chained:
                problem("event_unchained", row.seq)
            else:
                unchained += 1
            continue
        chained = True
        if row.prev_hash != before:
            problem("chain_broken", row.seq)
        try:
            if event_hash(row.prev_hash or "", document.id, row) != row.hash:
                problem("event_changed", row.seq)
        except crypto.CryptoError:
            problem("event_unreadable", row.seq)
        before = row.hash
    if (document.audit_head or None) != (before if chained else None):
        problem("history_cut")
    return found, len(events), unchained


def _tenants(session_factory: sessionmaker[Session]) -> list[tuple[str, str]]:
    with session_factory() as session:
        return list(session.execute(select(TenantRow.id, TenantRow.slug).order_by(TenantRow.slug)))


def _document_ids(session_factory: sessionmaker[Session], tenant_id: str) -> list[str]:
    with tenant_transaction(session_factory, tenant_id) as session:
        return list(
            session.scalars(
                select(DocumentRow.id)
                .where(DocumentRow.tenant_id == tenant_id)
                .order_by(DocumentRow.id)
            )
        )


def verify(session_factory: sessionmaker[Session]) -> Report:
    """Every tenant's originals and histories; nothing is written."""
    report = Report()
    heads = hashlib.sha256()
    for tenant_id, slug in _tenants(session_factory):
        for document_id in _document_ids(session_factory, tenant_id):
            with tenant_transaction(session_factory, tenant_id) as session:
                document = session.get(DocumentRow, document_id)
                found, events, unchained = _problems(document, slug)
                heads.update(f"{document_id}:{document.audit_head}\n".encode())
            report.documents += 1
            report.events += events
            report.unchained += unchained
            report.problems += found
    report.digest = heads.hexdigest()
    return report


@dataclass
class Chained:
    documents: int = 0
    events: int = 0
    # Documents left as they were: their chain is broken already, and chaining
    # their older events would hide that.
    refused: list[Problem] = field(default_factory=list)


def chain_older(session_factory: sessionmaker[Session], *, apply: bool) -> Chained:
    """Chain every history that has events from before the chain, from its
    first event on. Only where the rest of it verifies; without `apply` only
    counted."""
    result = Chained()
    for tenant_id, slug in _tenants(session_factory):
        for document_id in _document_ids(session_factory, tenant_id):
            with tenant_transaction(session_factory, tenant_id) as session:
                document = session.get(DocumentRow, document_id)
                found, _, unchained = _problems(document, slug)
                if not unchained:
                    continue
                if found:
                    result.refused += found
                    continue
                result.documents += 1
                result.events += unchained
                if not apply:
                    continue
                before = document.sha256
                for row in session.scalars(
                    select(AuditEventRow)
                    .where(AuditEventRow.document_id == document_id)
                    .order_by(AuditEventRow.seq)
                ):
                    row.prev_hash = before
                    row.hash = event_hash(before, document_id, row)
                    before = row.hash
                document.audit_head = before
    return result
