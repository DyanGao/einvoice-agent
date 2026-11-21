"""Seal what was stored before it was sealed, or open it all again (v1.0
Stage 6).

    einvoice seal                 how many values are not sealed (nothing written)
    einvoice seal --apply         seal them with each tenant's data key
    einvoice seal --open --apply  store them as they are again (before a downgrade,
                                  or leaving encryption: needs the master key)

Every sealed column (app/db/sealed.py) and the originals (`documents.content`),
each tenant in transactions of its own, acting for it.
"""

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import LargeBinary, Table, literal, select, type_coerce, update
from sqlalchemy.orm import Session, sessionmaker

from app import crypto
from app.db.rls import tenant_transaction
from app.db.sealed import Sealed
from app.db.tables import CONTENT_PLACE, Base, DocumentRow, TenantRow


@dataclass
class Report:
    # Per tenant slug: values sealed (or opened), and left as they were.
    changed: dict[str, int] = field(default_factory=dict)
    already: dict[str, int] = field(default_factory=dict)
    # Per place ("drafts.data"), over all tenants: values sealed (or opened).
    by_place: Counter = field(default_factory=Counter)


def sealed_columns() -> list[tuple[Table, str, Sealed]]:
    """Every column stored sealed, but the originals (which seal themselves)."""
    return [
        (table, column.name, column.type)
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, Sealed)
    ]


def _of_tenant(table: Table, tenant_id: str):
    """The table's rows that belong to the tenant."""
    documents = DocumentRow.__table__
    if "tenant_id" in table.c:
        return table.c.tenant_id == tenant_id
    return table.c.document_id.in_(select(documents.c.id).where(documents.c.tenant_id == tenant_id))


def _column(session: Session, table: Table, name: str, kind: Sealed, tenant_id: str, *,
            apply: bool, open_all: bool) -> tuple[int, int]:  # fmt: skip
    (key,) = table.primary_key.columns
    raw = type_coerce(table.c[name], LargeBinary)
    rows = session.execute(
        select(key, raw).where(_of_tenant(table, tenant_id), table.c[name].is_not(None))
    ).all()
    changed = already = 0
    for row_key, value in rows:
        if crypto.is_sealed(value) != open_all:
            already += 1
            continue
        changed += 1
        if not apply:
            continue
        plain = kind.open_raw(value)
        # Sealed again on the way in (the session acts for the tenant), or
        # written as the plain bytes, past the column type.
        new = literal(kind.plain_bytes(plain), LargeBinary) if open_all else plain
        session.execute(update(table).where(key == row_key).values({name: new}))
    return changed, already


def _originals(session: Session, tenant_id: str, *, apply: bool, open_all: bool) -> tuple[int, int]:
    changed = already = 0
    ids = session.scalars(select(DocumentRow.id).where(DocumentRow.tenant_id == tenant_id))
    for document_id in list(ids):
        document = session.get(DocumentRow, document_id)
        if crypto.is_sealed(document.sealed_content) != open_all:
            already += 1
            continue
        changed += 1
        if not apply:
            continue
        if open_all:
            document.sealed_content = document.content
        else:
            document.content = document.content  # sealed when written
    return changed, already


def seal_documents(
    session_factory: sessionmaker[Session], *, apply: bool, open_all: bool = False
) -> Report:
    """Seal every value stored plain (or, `open_all`, open every sealed one).
    Without `apply` only counted."""
    if crypto.current() is None:
        raise crypto.CryptoError("encryption is off (ENCRYPTION=off): there is no key to use")
    with session_factory() as session:
        tenants = list(session.execute(select(TenantRow.id, TenantRow.slug)))
    report = Report()
    for tenant_id, slug in tenants:
        changed = already = 0
        with tenant_transaction(session_factory, tenant_id) as session:
            done, kept = _originals(session, tenant_id, apply=apply, open_all=open_all)
            changed, already = changed + done, already + kept
            report.by_place[CONTENT_PLACE] += done
        for table, name, kind in sealed_columns():
            with tenant_transaction(session_factory, tenant_id) as session:
                done, kept = _column(
                    session, table, name, kind, tenant_id, apply=apply, open_all=open_all
                )
            changed, already = changed + done, already + kept
            report.by_place[kind.place] += done
        report.changed[slug], report.already[slug] = changed, already
    return report
