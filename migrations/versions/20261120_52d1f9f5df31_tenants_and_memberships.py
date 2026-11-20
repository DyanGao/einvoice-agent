"""tenants and memberships: one installation, several companies (v1.0 Stage 2,
increment 1). Everything that exists becomes tenant "default"; each user's
role becomes their membership in it.

Revision ID: 52d1f9f5df31
Revises: 41b4532e57ce
"""

import sqlalchemy as sa
from alembic import op

revision = "52d1f9f5df31"
down_revision = "41b4532e57ce"
branch_labels = None
depends_on = None

DEFAULT = "default"
# Tables whose rows belong to a tenant, as of this revision.
OWNED = ("documents", "seen_bank_transactions", "api_tokens", "sessions")
INDEXED = ("documents", "api_tokens", "sessions")


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.execute(
        "INSERT INTO tenants (id, slug, name, created_at) "
        "VALUES ('default', 'default', 'Default', now())"
    )
    op.create_table(
        "memberships",
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id", "tenant_id"),
    )
    op.execute(
        "INSERT INTO memberships (user_id, tenant_id, role, created_at) "
        "SELECT id, 'default', role, created_at FROM users"
    )
    op.drop_column("users", "role")

    for table in OWNED:
        # Existing rows are the default tenant's; new ones must say whose they are.
        op.add_column(
            table,
            sa.Column("tenant_id", sa.String(length=32), nullable=False, server_default=DEFAULT),
        )
        op.alter_column(table, "tenant_id", server_default=None)
        op.create_foreign_key(f"{table}_tenant_id_fkey", table, "tenants", ["tenant_id"], ["id"])
    for table in INDEXED:
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"], unique=False)

    # The same file sent to two companies is two documents.
    op.drop_constraint("documents_sha256_key", "documents", type_="unique")
    op.create_unique_constraint(
        "documents_tenant_id_sha256_key", "documents", ["tenant_id", "sha256"]
    )
    # Each tenant's ERP has its own bank transactions, and their ids may repeat.
    op.drop_constraint("seen_bank_transactions_pkey", "seen_bank_transactions", type_="primary")
    op.create_primary_key(
        "seen_bank_transactions_pkey", "seen_bank_transactions", ["tenant_id", "id"]
    )


def downgrade() -> None:
    others = op.get_bind().scalar(sa.text("SELECT count(*) FROM tenants WHERE id <> 'default'"))
    if others:
        raise RuntimeError(
            f"{others} tenant(s) besides 'default' exist; a single-tenant database cannot "
            "hold them. Remove them first, or restore a backup from before the upgrade."
        )
    op.drop_constraint("seen_bank_transactions_pkey", "seen_bank_transactions", type_="primary")
    op.create_primary_key("seen_bank_transactions_pkey", "seen_bank_transactions", ["id"])
    op.drop_constraint("documents_tenant_id_sha256_key", "documents", type_="unique")
    op.create_unique_constraint("documents_sha256_key", "documents", ["sha256"])
    for table in INDEXED:
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
    for table in OWNED:
        op.drop_constraint(f"{table}_tenant_id_fkey", table, type_="foreignkey")
        op.drop_column(table, "tenant_id")

    op.add_column("users", sa.Column("role", sa.String(length=20), nullable=True))
    # A user without a membership could not sign in; viewer is the least they had.
    op.execute(
        "UPDATE users SET role = coalesce("
        "(SELECT role FROM memberships m WHERE m.user_id = users.id AND m.tenant_id = 'default'),"
        " 'viewer')"
    )
    op.alter_column("users", "role", nullable=False)
    op.drop_table("memberships")
    op.drop_table("tenants")
