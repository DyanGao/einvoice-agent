"""user language: the review UI's language a person chose, kept for their
next sign-in in any browser (v1.0 Stage 4, increment 4). NULL until they
choose; then the browser's language decides.

Revision ID: c41e7d2a9b15
Revises: fbff682c50f6
"""

import sqlalchemy as sa
from alembic import op

revision = "c41e7d2a9b15"
down_revision = "fbff682c50f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("language", sa.String(length=5), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "language")
