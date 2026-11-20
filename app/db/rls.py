"""Row-level security in Postgres (v1.0 Stage 2, increment 2).

The application filters every query by tenant. This is the second wall: a
transaction that acts for a tenant switches to the role `einvoice_tenant`
(`act_for`), and Postgres itself hides and refuses every other tenant's row,
whatever the query says.

- `documents` and `seen_bank_transactions` carry `tenant_id`; their policy
  compares it with the transaction's `app.tenant_id`.
- The tables hanging off a document (drafts, findings, …) are visible where
  their document is: their policy asks for the document, and the document's
  own policy answers. No second `tenant_id` to keep in step.
- `einvoice_tenant` cannot log in and has no BYPASSRLS. It may read and
  change only these tables (and read `tenants`): not users, passwords, tokens
  or sessions. The login role (which may be a superuser, as in Compose) only
  takes it on for the transaction, with SET LOCAL ROLE.

Background work (worker, folder watch, bank watch, delivery loop, the CLI)
runs in `tenant_transaction`s too. Only what has to see across tenants runs
as the login role: finding which tenant a queued document belongs to, the
sweep for documents whose job was lost, the sign-in tables, migrations.

The migration `migrations/versions/20261120_*_row_level_security.py` installs
the same statements in migrated databases; databases made from the tables
(tests on Postgres) get them from `install` below. tests/test_rls.py checks
that both end up with the same policies.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

TENANT_ROLE = "einvoice_tenant"
SETTING = "app.tenant_id"

TENANT_TABLES = ("documents", "seen_bank_transactions")
DOCUMENT_TABLES = (
    "invoices",
    "drafts",
    "validation_results",
    "audit_events",
    "findings",
    "matches",
    "decisions",
    "deliveries",
)

_CURRENT = f"current_setting('{SETTING}', true)"


def statements() -> list[str]:
    """Everything `install` runs, in order. Only this module's constants are
    put into the SQL, never input."""
    out = [
        f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{TENANT_ROLE}') THEN
                CREATE ROLE {TENANT_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END $$""",  # noqa: S608 -- constants only
        # The login role may take the tenant role on (a superuser always may).
        f"GRANT {TENANT_ROLE} TO CURRENT_USER",
        f"GRANT USAGE ON SCHEMA public TO {TENANT_ROLE}",
        f"GRANT SELECT ON tenants TO {TENANT_ROLE}",
        f"GRANT USAGE, SELECT ON SEQUENCE audit_events_seq_seq TO {TENANT_ROLE}",
    ]
    for table in (*TENANT_TABLES, *DOCUMENT_TABLES):
        out.append(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {TENANT_ROLE}")
        out.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        out.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for table in TENANT_TABLES:
        rule = f"tenant_id = {_CURRENT}"
        out.append(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")
    for table in DOCUMENT_TABLES:
        rule = f"EXISTS (SELECT 1 FROM documents d WHERE d.id = {table}.document_id)"  # noqa: S608
        out.append(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")
    return out


def install(target, connection, **_) -> None:
    """After `create_all` on Postgres: the same policies the migration makes."""
    if connection.dialect.name != "postgresql":
        return
    for statement in statements():
        connection.execute(text(statement))


def act_for(session: Session, tenant_id: str) -> None:
    """From here to the end of the transaction, the session acts for the
    tenant, and Postgres shows and accepts only that tenant's rows. Nothing
    on SQLite, which has no row-level security (the application filter is
    all there is there)."""
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(text(f"SET LOCAL ROLE {TENANT_ROLE}"))
    session.execute(
        text("SELECT set_config(:name, :tenant, true)"),
        {
            "name": SETTING,
            "tenant": tenant_id,
        },
    )


@contextmanager
def tenant_transaction(session_factory: sessionmaker[Session], tenant_id: str) -> Iterator[Session]:
    """A transaction acting for the tenant from its first statement:
    committed at the end, rolled back on an exception."""
    with session_factory.begin() as session:
        act_for(session, tenant_id)
        yield session
