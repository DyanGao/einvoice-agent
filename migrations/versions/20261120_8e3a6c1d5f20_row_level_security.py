"""row-level security: Postgres itself keeps each tenant's rows apart (v1.0
Stage 2, increment 2). See app/db/rls.py; the statements are copied here so
this migration stays as it is when that module changes. tests/test_rls.py
checks that a migrated database and one made from the tables agree.

Revision ID: 8e3a6c1d5f20
Revises: 52d1f9f5df31
"""

from alembic import op

revision = "8e3a6c1d5f20"
down_revision = "52d1f9f5df31"
branch_labels = None
depends_on = None

ROLE = "einvoice_tenant"
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
CURRENT = "current_setting('app.tenant_id', true)"


def upgrade() -> None:
    # Roles belong to the whole Postgres server, so it may exist already.
    op.execute(
        f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END $$"""  # noqa: S608 -- constants only
    )
    op.execute(f"GRANT {ROLE} TO CURRENT_USER")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    op.execute(f"GRANT SELECT ON tenants TO {ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE audit_events_seq_seq TO {ROLE}")
    for table in (*TENANT_TABLES, *DOCUMENT_TABLES):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for table in TENANT_TABLES:
        rule = f"tenant_id = {CURRENT}"
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")
    for table in DOCUMENT_TABLES:
        rule = f"EXISTS (SELECT 1 FROM documents d WHERE d.id = {table}.document_id)"  # noqa: S608
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def downgrade() -> None:
    for table in (*TENANT_TABLES, *DOCUMENT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM {ROLE}")
    op.execute(f"REVOKE ALL ON SEQUENCE audit_events_seq_seq FROM {ROLE}")
    op.execute(f"REVOKE ALL ON tenants FROM {ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
    # The role itself stays: it belongs to the server, and other databases on
    # it (a test database, another installation) may still use it.
