"""drafts for documents processed before drafts existed

Revision ID: 3b6e1f0c9a42
Revises: 97d17be5bbbf
"""

from alembic import op

revision = "3b6e1f0c9a42"
down_revision = "97d17be5bbbf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Review reads and edits the draft. Documents parsed before the drafts
    # table existed have an invoice but no draft, so the review UI showed them
    # with empty fields. Their parsed invoice is what the machine produced.
    op.execute(
        """
        INSERT INTO drafts (id, document_id, original, data, updated_at)
        SELECT i.id, i.document_id, i.data, i.data, CURRENT_TIMESTAMP
        FROM invoices i
        WHERE NOT EXISTS (SELECT 1 FROM drafts d WHERE d.document_id = i.document_id)
        """
    )


def downgrade() -> None:
    # Backfilled drafts cannot be told apart from later ones, and the previous
    # revision works with them in place: nothing to undo.
    pass
