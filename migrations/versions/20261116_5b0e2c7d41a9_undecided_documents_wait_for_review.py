"""undecided documents wait for review: a document processed without the
checks was marked as needing no review although nothing decided it, and with
no decision on record it was never delivered either. It goes back to the
review queue, where a person checks it again or approves it.

Revision ID: 5b0e2c7d41a9
Revises: e63f1f641d14
"""

from alembic import op

revision = "5b0e2c7d41a9"
down_revision = "e63f1f641d14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE documents SET review_state = 'pending' "
        "WHERE review_state = 'not_required' "
        "AND id NOT IN (SELECT document_id FROM decisions)"
    )


def downgrade() -> None:
    # Which pending documents were marked not_required before is not recorded;
    # leaving them in the review queue loses nothing.
    pass
