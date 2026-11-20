"""mailbox state: how far the mailbox poller has read each tenant's mailbox
folder (v1.0 Stage 3, increment 1), with the same row-level security as the
other tenant tables.

Revision ID: 788ccbc609f2
Revises: 747922bf6637
"""

import sqlalchemy as sa
from alembic import op

revision = "788ccbc609f2"
down_revision = "747922bf6637"
branch_labels = None
depends_on = None

ROLE = "einvoice_tenant"
RULE = "tenant_id = current_setting('app.tenant_id', true)"


def upgrade() -> None:
    op.create_table(
        "mailbox_state",
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("mailbox", sa.String(length=400), nullable=False),
        sa.Column("uidvalidity", sa.BigInteger(), nullable=False),
        sa.Column("last_uid", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("tenant_id", "mailbox"),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON mailbox_state TO {ROLE}")
    op.execute("ALTER TABLE mailbox_state ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE mailbox_state FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON mailbox_state USING ({RULE}) WITH CHECK ({RULE})"
    )


def downgrade() -> None:
    op.drop_table("mailbox_state")
