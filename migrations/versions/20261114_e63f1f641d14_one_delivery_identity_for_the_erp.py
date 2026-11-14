"""one delivery identity for the ERP: deliveries of the ERP API connector
were named erp_api; both ways into the ERP (API and screens) are now erp,
so that switching the channel cannot book an invoice a second time

Revision ID: e63f1f641d14
Revises: 414e8ca53d26
"""

from alembic import op

revision = "e63f1f641d14"
down_revision = "414e8ca53d26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE deliveries SET connector = 'erp' WHERE connector = 'erp_api'")


def downgrade() -> None:
    # Only the API existed before: every ERP delivery was made through it.
    op.execute("UPDATE deliveries SET connector = 'erp_api' WHERE connector = 'erp'")
