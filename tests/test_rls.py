"""Row-level security (v1.0 Stage 2, increment 2): Postgres alone keeps the
tenants apart, with no help from the application's own filter.

Postgres only (make test-pg, and CI). Every query here deliberately leaves
out `WHERE tenant_id = …`, or asks for the wrong tenant on purpose.
"""

import os
from datetime import UTC, date, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app import db
from app.db import rls
from app.db.duplicates import redundant_copies
from app.rematch import open_invoices

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="row-level security needs Postgres"
)

A, B = db.DEFAULT_TENANT_ID, "acme"
TABLES = (*rls.TENANT_TABLES, *rls.DOCUMENT_TABLES)


def document(tenant_id: str, key: str) -> db.DocumentRow:
    """A document with a row in every table that hangs off one."""
    data = {"number": f"RE-{key}", "gross_total": "119.00"}
    row = db.DocumentRow(
        id=f"doc-{key}",
        tenant_id=tenant_id,
        sha256=key * 64,
        filename=f"{key}.pdf",
        format="pdf_text",
        status="extracted",
        content=b"%PDF-",
        review_state="pending",
        draft=db.DraftRow(original=data, data=data),
        validation=db.ValidationRow(valid=True, scenario=None, violations=[]),
        match=db.MatchRow(
            supplier_id=f"S-{tenant_id}",
            po_basis="none",
            payments=[],
            payment_basis="none",
            explanation=[],
        ),
        decision=db.DecisionRow(route="review", reasons=[], version="test"),
    )
    row.findings.append(
        db.FindingRow(
            position=0, type="duplicate", severity="review", message="m", fields=[], detail={}
        )
    )
    row.audit_events.append(db.AuditEventRow(event="received", actor="system", detail={}))
    row.deliveries.append(
        db.DeliveryRow(
            connector="erp",
            idempotency_key=f"key-{key}",
            status="pending",
            booking={},
            next_attempt_at=datetime.now(UTC),
        )
    )
    row.invoice = db.InvoiceRow(
        number=f"RE-{key}",
        issue_date=date(2026, 11, 1),
        seller_name="S",
        currency="EUR",
        gross_total=119,
        data=data,
    )
    return row


@pytest.fixture
def seeded(session_factory):
    """Two tenants, each with one fully populated document and one seen bank
    transaction, written as the login role (not limited by the policies)."""
    with session_factory.begin() as session:
        session.add(db.TenantRow(id=B, slug="acme", name="Acme GmbH"))
        session.flush()
        session.add_all([document(A, "a"), document(B, "b")])
        session.add_all(
            [
                db.SeenBankTransactionRow(tenant_id=t, id="tx-1", booking_date=date(2026, 11, 1))
                for t in (A, B)
            ]
        )
    return session_factory


def as_tenant(session_factory, tenant_id):
    session = session_factory()
    session.begin()
    rls.act_for(session, tenant_id)
    return session


def test_every_tenant_table_has_its_policy_and_forces_it(session_factory):
    with session_factory() as session:
        forced = dict(
            session.execute(
                text("SELECT relname, relforcerowsecurity FROM pg_class WHERE relname = ANY(:t)"),
                {"t": list(TABLES)},
            ).all()
        )
        policies = set(
            session.scalars(
                text("SELECT tablename FROM pg_policies WHERE policyname = 'tenant_isolation'")
            )
        )
    assert forced == dict.fromkeys(TABLES, True)
    assert policies == set(TABLES)


@pytest.mark.parametrize("table", TABLES)
def test_a_tenant_sees_only_its_own_rows_without_any_filter(seeded, table):
    with as_tenant(seeded, A) as session:
        mine = session.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
    with seeded() as owner:
        everyone = owner.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
    assert (mine, everyone) == (1, 2)


def test_another_tenants_document_does_not_exist_for_it(seeded):
    with as_tenant(seeded, A) as session:
        assert session.get(db.DocumentRow, "doc-b") is None
        assert session.scalars(select(db.DocumentRow.id)).all() == ["doc-a"]


def test_asking_for_the_wrong_tenant_on_purpose_still_finds_nothing(seeded):
    """The application filter says B; the transaction acts for A."""
    with as_tenant(seeded, A) as session:
        assert open_invoices(session, B, f"S-{B}") == []
        assert redundant_copies(session, B) == []


def test_another_tenants_rows_cannot_be_changed_or_deleted(seeded):
    with as_tenant(seeded, A) as session:
        changed = session.execute(update(db.DocumentRow).values(review_state="approved")).rowcount
        deleted = session.execute(text("DELETE FROM findings")).rowcount
        session.commit()
    with seeded() as owner:
        states = dict(owner.execute(select(db.DocumentRow.id, db.DocumentRow.review_state)).all())
        findings = owner.scalar(select(func.count()).select_from(db.FindingRow))
    assert (changed, deleted) == (1, 1)
    assert states == {"doc-a": "approved", "doc-b": "pending"}
    assert findings == 1


def test_a_document_cannot_be_stored_for_another_tenant(seeded):
    with as_tenant(seeded, A) as session:
        session.add(document(B, "c"))
        with pytest.raises(DBAPIError, match="row-level security"):
            session.flush()


def test_nothing_can_be_attached_to_another_tenants_document(seeded):
    with as_tenant(seeded, A) as session:
        session.add(db.AuditEventRow(document_id="doc-b", event="x", actor="mallory", detail={}))
        with pytest.raises(DBAPIError, match="row-level security"):
            session.flush()


def test_without_a_tenant_the_role_sees_nothing(seeded):
    with as_tenant(seeded, "") as session:
        assert session.scalar(select(func.count()).select_from(db.DocumentRow)) == 0


@pytest.mark.parametrize("table", ["users", "memberships", "api_tokens", "sessions"])
def test_the_tenant_role_cannot_read_accounts_or_secrets(seeded, table):
    with as_tenant(seeded, A) as session, pytest.raises(DBAPIError, match="permission denied"):
        session.execute(text(f"SELECT 1 FROM {table}"))  # noqa: S608


def test_the_tenant_setting_ends_with_the_transaction(seeded):
    session = as_tenant(seeded, A)
    session.commit()
    # Back to the login role, which sees all (it is not limited by the policies).
    assert session.scalar(select(func.count()).select_from(db.DocumentRow)) == 2
    assert session.scalar(text("SELECT current_user")) != rls.TENANT_ROLE
    session.close()


def _policy_state(url: str) -> list[tuple]:
    engine = create_engine(url)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, p.polcmd,"
                " pg_get_expr(p.polqual, p.polrelid), pg_get_expr(p.polwithcheck, p.polrelid),"
                " has_table_privilege(:role, c.oid, 'SELECT'),"
                " has_table_privilege(:role, c.oid, 'INSERT')"
                " FROM pg_class c LEFT JOIN pg_policy p ON p.polrelid = c.oid"
                " WHERE c.relkind = 'r' AND c.relnamespace = 'public'::regnamespace"
                " AND c.relname <> 'alembic_version' ORDER BY 1"
            ),
            {"role": rls.TENANT_ROLE},
        ).all()
    engine.dispose()
    return [tuple(row) for row in rows]


def test_a_migrated_database_has_the_same_policies_as_one_made_from_the_tables(
    session_factory, monkeypatch
):
    made = os.environ["TEST_DATABASE_URL"]
    migrated = make_url(made).set(database=make_url(made).database + "_migrated")
    admin = create_engine(made, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{migrated.database}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{migrated.database}"'))
    try:
        monkeypatch.setenv("DATABASE_URL", migrated.render_as_string(hide_password=False))
        command.upgrade(Config("alembic.ini"), "head")
        assert _policy_state(migrated.render_as_string(hide_password=False)) == _policy_state(made)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{migrated.database}" WITH (FORCE)'))
        admin.dispose()
