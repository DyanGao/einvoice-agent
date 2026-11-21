"""Seal what was stored before it was sealed, or open it all again (v1.0
Stage 6).

    einvoice seal                 how many documents are not sealed (nothing written)
    einvoice seal --apply         seal them with each tenant's data key
    einvoice seal --open --apply  store them as they are again (before a downgrade,
                                  or leaving encryption: needs the master key)

Each tenant in transactions of its own, acting for it, a batch at a time.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import crypto
from app.db.rls import tenant_transaction
from app.db.tables import DocumentRow, TenantRow

BATCH = 200


@dataclass
class Report:
    # Per tenant slug: documents sealed (or opened), and left as they were.
    changed: dict[str, int] = field(default_factory=dict)
    already: dict[str, int] = field(default_factory=dict)


def _ids(session_factory: sessionmaker[Session], tenant_id: str) -> list[str]:
    with tenant_transaction(session_factory, tenant_id) as session:
        return list(
            session.scalars(
                select(DocumentRow.id)
                .where(DocumentRow.tenant_id == tenant_id)
                .order_by(DocumentRow.id)
            )
        )


def seal_documents(
    session_factory: sessionmaker[Session], *, apply: bool, open_all: bool = False
) -> Report:
    """Seal every document's original that is stored plain (or, `open_all`,
    open every sealed one). Without `apply` only counted."""
    if crypto.current() is None:
        raise crypto.CryptoError("encryption is off (ENCRYPTION=off): there is no key to use")
    with session_factory() as session:
        tenants = list(session.execute(select(TenantRow.id, TenantRow.slug)))
    report = Report()
    for tenant_id, slug in tenants:
        ids = _ids(session_factory, tenant_id)
        changed = already = 0
        for start in range(0, len(ids), BATCH):
            with tenant_transaction(session_factory, tenant_id) as session:
                for document_id in ids[start : start + BATCH]:
                    document = session.get(DocumentRow, document_id)
                    sealed = crypto.is_sealed(document.sealed_content)
                    if sealed != open_all:
                        already += 1
                        continue
                    changed += 1
                    if not apply:
                        continue
                    if open_all:
                        document.sealed_content = document.content
                    else:
                        document.content = document.content  # sealed when written
        report.changed[slug], report.already[slug] = changed, already
    return report
