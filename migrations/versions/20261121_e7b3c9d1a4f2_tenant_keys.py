"""tenant keys: each tenant's data key, wrapped by the master key (v1.0
Stage 6, increment 1). The installation's table: no grant to the tenant role,
no row-level security (a wrapped key opens nothing without the master key).

Existing documents stay readable as they are; `einvoice seal --apply` seals
them with the master key in the environment.

Revision ID: e7b3c9d1a4f2
Revises: d5a8e2f41c07
"""

import sqlalchemy as sa
from alembic import op

revision = "e7b3c9d1a4f2"
down_revision = "d5a8e2f41c07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_keys",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("wrapped", sa.LargeBinary(), nullable=False),
        sa.Column("master_id", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "version"),
    )
    op.create_index(op.f("ix_tenant_keys_tenant_id"), "tenant_keys", ["tenant_id"], unique=False)


def downgrade() -> None:
    # Without its key a sealed document cannot be read again: never drop them.
    sealed = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM documents WHERE substring(content from 1 for 4) = :magic"),
        {"magic": b"EIA1"},
    )
    if sealed:
        raise RuntimeError(
            f"{sealed} documents are sealed with the keys in tenant_keys; downgrading would make "
            "them unreadable. Open them first: einvoice seal --open --apply (with MASTER_KEY set)."
        )
    op.drop_index(op.f("ix_tenant_keys_tenant_id"), table_name="tenant_keys")
    op.drop_table("tenant_keys")
