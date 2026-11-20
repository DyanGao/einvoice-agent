"""mails: every mail the mailbox poller read, and what became of each of its
attached files (v1.0 Stage 3, increment 2), with the same row-level security
as the other tenant tables.

Revision ID: a3bdd3a834c6
Revises: 788ccbc609f2
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a3bdd3a834c6"
down_revision = "788ccbc609f2"
branch_labels = None
depends_on = None

ROLE = "einvoice_tenant"
RULE = "tenant_id = current_setting('app.tenant_id', true)"


def upgrade() -> None:
    op.create_table(
        "mails",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("mailbox", sa.String(length=400), nullable=False),
        sa.Column("uidvalidity", sa.BigInteger(), nullable=False),
        sa.Column("uid", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.String(length=300), nullable=False),
        sa.Column("sender", sa.String(length=300), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("sent", sa.String(length=300), nullable=False),
        sa.Column(
            "parts",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "mailbox", "uidvalidity", "uid"),
    )
    op.create_index(op.f("ix_mails_created_at"), "mails", ["created_at"], unique=False)
    op.create_index(op.f("ix_mails_tenant_id"), "mails", ["tenant_id"], unique=False)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON mails TO {ROLE}")
    op.execute("ALTER TABLE mails ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE mails FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON mails USING ({RULE}) WITH CHECK ({RULE})")


def downgrade() -> None:
    op.drop_index(op.f("ix_mails_tenant_id"), table_name="mails")
    op.drop_index(op.f("ix_mails_created_at"), table_name="mails")
    op.drop_table("mails")
