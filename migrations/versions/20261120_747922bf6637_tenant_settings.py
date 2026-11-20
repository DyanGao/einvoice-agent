"""tenant settings: each company's ERP, connectors, page limit (v1.0 Stage 2,
increment 4), with the same row-level security as the other tenant tables.
No row for the default tenant: it keeps using .env until one is set.

Revision ID: 747922bf6637
Revises: 8e3a6c1d5f20
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "747922bf6637"
down_revision = "8e3a6c1d5f20"
branch_labels = None
depends_on = None

ROLE = "einvoice_tenant"
RULE = "tenant_id = current_setting('app.tenant_id', true)"


def upgrade() -> None:
    op.create_table(
        "tenant_settings",
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column(
            "config",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_settings TO {ROLE}")
    op.execute("ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON tenant_settings USING ({RULE}) WITH CHECK ({RULE})"
    )


def downgrade() -> None:
    op.drop_table("tenant_settings")
