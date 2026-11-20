"""texts as codes: next to each English sentence the server writes for people
(a finding's message, a match's explanation, a delivery's error), its code and
parameters, so the review UI can show it in German or English (v1.0 Stage 4,
increment 2). Rows from before keep NULL and show the English sentence.

Revision ID: fbff682c50f6
Revises: a3bdd3a834c6
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "fbff682c50f6"
down_revision = "a3bdd3a834c6"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("deliveries", sa.Column("error_text", JSON, nullable=True))
    op.add_column("findings", sa.Column("text", JSON, nullable=True))
    op.add_column("matches", sa.Column("explanation_texts", JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("matches", "explanation_texts")
    op.drop_column("findings", "text")
    op.drop_column("deliveries", "error_text")
