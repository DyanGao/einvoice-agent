"""audit chain: each history event's hash and the one before it, and each
document's newest hash (v1.0 Stage 6, increment 4, app/db/chain.py).

Events stored before have no hash; `einvoice chain --apply` chains them (it
needs the master key: the hash covers the event's sealed detail as it reads).

Revision ID: a9d4e1b7c3f6
Revises: f2c8a6e3b5d1
"""

import sqlalchemy as sa
from alembic import op

revision = "a9d4e1b7c3f6"
down_revision = "f2c8a6e3b5d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("prev_hash", sa.String(length=64), nullable=True))
    op.add_column("audit_events", sa.Column("hash", sa.String(length=64), nullable=True))
    op.add_column("documents", sa.Column("audit_head", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "audit_head")
    op.drop_column("audit_events", "hash")
    op.drop_column("audit_events", "prev_hash")
