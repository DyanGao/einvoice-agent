"""credit links: the invoice a credit note corrects, as the checks found it
(v1.0 Stage 5, increment 2), with the same row-level security as the other
tables of a document.

Revision ID: d5a8e2f41c07
Revises: c41e7d2a9b15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d5a8e2f41c07"
down_revision = "c41e7d2a9b15"
branch_labels = None
depends_on = None

ROLE = "einvoice_tenant"
# As app/db/rls.py: visible where its document is.
RULE = "EXISTS (SELECT 1 FROM documents d WHERE d.id = credit_links.document_id)"


def upgrade() -> None:
    op.create_table(
        "credit_links",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("document_id", sa.String(length=32), nullable=False),
        sa.Column("invoice_document_id", sa.String(length=32), nullable=True),
        sa.Column("basis", sa.String(length=20), nullable=False),
        sa.Column("explanation", sa.String(), nullable=False),
        sa.Column(
            "explanation_text",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["invoice_document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id"),
    )
    op.create_index(
        op.f("ix_credit_links_invoice_document_id"),
        "credit_links",
        ["invoice_document_id"],
        unique=False,
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON credit_links TO {ROLE}")
    op.execute("ALTER TABLE credit_links ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE credit_links FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON credit_links USING ({RULE}) WITH CHECK ({RULE})")


def downgrade() -> None:
    op.drop_index(op.f("ix_credit_links_invoice_document_id"), table_name="credit_links")
    op.drop_table("credit_links")
