"""retention: a legal hold per document, and the append-only deletion log
(v1.0 Stage 6, increment 5, app/retention.py). The log is the tenant's
(row-level security, read and add only), and a trigger refuses every change
to it, by any role. The statements are app/db/rls.py's, frozen here.

Revision ID: b6e2f8a4d1c9
Revises: a9d4e1b7c3f6
"""

import sqlalchemy as sa
from alembic import op

revision = "b6e2f8a4d1c9"
down_revision = "a9d4e1b7c3f6"
branch_labels = None
depends_on = None

ROLE = "einvoice_tenant"
RULE = "tenant_id = current_setting('app.tenant_id', true)"


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("legal_hold", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "deletion_log",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("document_id", sa.String(length=32), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("audit_head", sa.String(length=64), nullable=True),
        sa.Column("events", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_end", sa.Date(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_by", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_deletion_log_tenant_id"), "deletion_log", ["tenant_id"], unique=False)
    op.execute(
        """CREATE OR REPLACE FUNCTION einvoice_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END $$ LANGUAGE plpgsql"""
    )
    op.execute(f"GRANT SELECT, INSERT ON deletion_log TO {ROLE}")
    op.execute("ALTER TABLE deletion_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE deletion_log FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON deletion_log USING ({RULE}) WITH CHECK ({RULE})")
    op.execute(
        "CREATE TRIGGER deletion_log_append_only BEFORE UPDATE OR DELETE ON deletion_log"
        " FOR EACH ROW EXECUTE FUNCTION einvoice_append_only()"
    )
    op.execute(
        "CREATE TRIGGER deletion_log_no_truncate BEFORE TRUNCATE ON deletion_log"
        " FOR EACH STATEMENT EXECUTE FUNCTION einvoice_append_only()"
    )


def downgrade() -> None:
    # The log itself is what downgrading gives up: refuse while it holds anything.
    logged = op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM deletion_log"))
    if logged:
        raise RuntimeError(f"the deletion log holds {logged} entries; it is not given up")
    op.drop_index(op.f("ix_deletion_log_tenant_id"), table_name="deletion_log")
    op.drop_table("deletion_log")
    op.execute("DROP FUNCTION IF EXISTS einvoice_append_only()")
    op.drop_column("documents", "legal_hold")
