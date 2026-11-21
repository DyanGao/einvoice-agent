"""Seal what was stored before it was sealed, or open it all again (v1.0
Stage 6).

    einvoice seal                 how many values are not sealed (nothing written)
    einvoice seal --apply         seal them with each tenant's data key
    einvoice seal --open --apply  store them as they are again (before a downgrade,
                                  or leaving encryption: needs the master key)

Every sealed column (app/db/sealed.py) and the originals (`documents.content`),
each tenant in transactions of its own, acting for it. A value sealed with
an older data key of its tenant (after `einvoice key-rotate-tenant`), or with
another tenant's, is sealed again with the tenant's current key.
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
    # Per tenant slug: of `changed`, those sealed already, but not with its current key.
    rekeyed: dict[str, int] = field(default_factory=dict)


def _to_change(value: bytes, *, open_all: bool, current_key: str | None) -> tuple[bool, bool]:
    """Whether the value is to be written again, and whether because of its key."""
    if open_all:
        return crypto.is_sealed(value), False
    if not crypto.is_sealed(value):
        return True, False
    rekey = crypto.key_id_of(value) != current_key
    return rekey, rekey


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


def _column(
    session: Session,
    table: Table,
    name: str,
    kind: Sealed,
    tenant_id: str,
    *,
    apply: bool,
    open_all: bool,
    current_key: str | None,
) -> tuple[int, int, int]:
    (key,) = table.primary_key.columns
    raw = type_coerce(table.c[name], LargeBinary)
    rows = session.execute(
        select(key, raw).where(_of_tenant(table, tenant_id), table.c[name].is_not(None))
    ).all()
    changed = already = rekeyed = 0
    for row_key, value in rows:
        change, rekey = _to_change(value, open_all=open_all, current_key=current_key)
        if not change:
            already += 1
            continue
        changed += 1
        rekeyed += rekey
        if not apply:
            continue
        plain = kind.open_raw(value)
        # Sealed again on the way in (the session acts for the tenant), or
        # written as the plain bytes, past the column type.
        new = literal(kind.plain_bytes(plain), LargeBinary) if open_all else plain
        session.execute(update(table).where(key == row_key).values({name: new}))
    return changed, already, rekeyed


def _originals(
    session: Session, tenant_id: str, *, apply: bool, open_all: bool, current_key: str | None
) -> tuple[int, int, int]:
    changed = already = rekeyed = 0
    ids = session.scalars(select(DocumentRow.id).where(DocumentRow.tenant_id == tenant_id))
    for document_id in list(ids):
        document = session.get(DocumentRow, document_id)
        change, rekey = _to_change(
            document.sealed_content, open_all=open_all, current_key=current_key
        )
        if not change:
            already += 1
            continue
        changed += 1
        rekeyed += rekey
        if not apply:
            continue
        if open_all:
            document.sealed_content = document.content
        else:
            document.content = document.content  # sealed when written
    return changed, already, rekeyed


def seal_documents(
    session_factory: sessionmaker[Session],
    *,
    apply: bool,
    open_all: bool = False,
    tenant_ids: list[str] | None = None,
) -> Report:
    """Seal every value stored plain or under another than its tenant's
    current key (or, `open_all`, open every sealed one); all tenants, or those
    of `tenant_ids`. Without `apply` only counted."""
    keyring = crypto.current()
    if keyring is None:
        raise crypto.CryptoError("encryption is off (ENCRYPTION=off): there is no key to use")
    with session_factory() as session:
        query = select(TenantRow.id, TenantRow.slug)
        if tenant_ids is not None:
            query = query.where(TenantRow.id.in_(tenant_ids))
        tenants = list(session.execute(query))
    report = Report()
    for tenant_id, slug in tenants:
        # Without `apply` no key is made for a tenant that has none yet.
        stored = keyring.store.current(tenant_id)
        current_key = keyring.key_for(tenant_id) if apply or stored else None
        options = {"apply": apply, "open_all": open_all, "current_key": current_key}
        changed = already = rekeyed = 0
        with tenant_transaction(session_factory, tenant_id) as session:
            done, kept, rekey = _originals(session, tenant_id, **options)
        changed, already, rekeyed = changed + done, already + kept, rekeyed + rekey
        report.by_place[CONTENT_PLACE] += done
        for table, name, kind in sealed_columns():
            with tenant_transaction(session_factory, tenant_id) as session:
                done, kept, rekey = _column(session, table, name, kind, tenant_id, **options)
            changed, already, rekeyed = changed + done, already + kept, rekeyed + rekey
            report.by_place[kind.place] += done
        report.changed[slug], report.already[slug] = changed, already
        report.rekeyed[slug] = rekeyed
    return report
