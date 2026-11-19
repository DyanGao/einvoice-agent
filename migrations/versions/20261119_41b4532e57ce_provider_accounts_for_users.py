"""provider accounts for users: the identity provider's account a user is
linked to at their first sign-in through it (v1.0 Stage 1, increment 2)

Revision ID: 41b4532e57ce
Revises: 7c1d9e4a2b6f
"""

import sqlalchemy as sa
from alembic import op

revision = "41b4532e57ce"
down_revision = "7c1d9e4a2b6f"
branch_labels = None
depends_on = None

# Postgres' own name for the constraint, so it matches what the tables declare.
CONSTRAINT = "users_oidc_issuer_oidc_subject_key"


def upgrade() -> None:
    op.add_column("users", sa.Column("oidc_issuer", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("oidc_subject", sa.String(length=255), nullable=True))
    op.create_unique_constraint(CONSTRAINT, "users", ["oidc_issuer", "oidc_subject"])


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "users", type_="unique")
    op.drop_column("users", "oidc_subject")
    op.drop_column("users", "oidc_issuer")
