"""sealed invoice data: the columns that hold what an invoice says become
bytes, to be stored sealed with the tenant's data key (v1.0 Stage 6,
increment 2, app/db/sealed.py). The invoices table loses its plain copies of
number, dates, seller and totals: nothing searched by them, and `data` holds
the whole invoice.

The migration has no key: it stores each value as the bytes of its text or
JSON, which the application reads as stored before sealing; `einvoice seal
--apply` then seals them.

Revision ID: f2c8a6e3b5d1
Revises: e7b3c9d1a4f2
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f2c8a6e3b5d1"
down_revision = "e7b3c9d1a4f2"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
# (table, column, its type before, nullable)
COLUMNS = (
    ("documents", "filename", sa.String(length=255), True),
    ("documents", "error", sa.String(), True),
    ("invoices", "data", JSONB, False),
    ("drafts", "original", JSONB, False),
    ("drafts", "data", JSONB, False),
    ("validation_results", "violations", JSONB, False),
    ("audit_events", "detail", JSONB, False),
    ("findings", "message", sa.String(), False),
    ("findings", "text", JSONB, True),
    ("findings", "detail", JSONB, False),
    ("credit_links", "explanation", sa.String(), False),
    ("credit_links", "explanation_text", JSONB, True),
    ("matches", "po_number", sa.String(length=50), True),
    ("matches", "payments", JSONB, False),
    ("matches", "explanation", JSONB, False),
    ("matches", "explanation_texts", JSONB, True),
    ("decisions", "reasons", JSONB, False),
    ("deliveries", "booking", JSONB, False),
    ("deliveries", "last_error", sa.String(), True),
    ("deliveries", "error_text", JSONB, True),
    ("mails", "sender", sa.String(length=300), False),
    ("mails", "subject", sa.String(length=300), False),
    ("mails", "sent", sa.String(length=300), False),
    ("mails", "parts", JSONB, False),
)
# The invoices table's plain copies, as the initial schema made them.
INVOICE_COPIES = (
    ("number", sa.String(length=255), "data->>'number'", False),
    ("issue_date", sa.Date(), "(data->>'issue_date')::date", False),
    ("seller_name", sa.String(length=255), "data->'seller'->>'name'", False),
    ("seller_vat_id", sa.String(length=32), "data->'seller'->>'vat_id'", True),
    ("currency", sa.String(length=3), "data->>'currency'", False),
    ("gross_total", sa.Numeric(precision=18, scale=4), "(data->>'gross_total')::numeric", False),
)


def upgrade() -> None:
    for table, column, _, _ in COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.LargeBinary(),
            postgresql_using=f"convert_to({column}::text, 'UTF8')",
        )
    op.drop_index(op.f("ix_invoices_seller_vat_id"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_number"), table_name="invoices")
    for column, _, _, _ in INVOICE_COPIES:
        op.drop_column("invoices", column)


def downgrade() -> None:
    bind = op.get_bind()
    sealed = {
        f"{table}.{column}": count
        for table, column, _, _ in COLUMNS
        if (
            count := bind.scalar(
                sa.text(
                    f"SELECT COUNT(*) FROM {table} "  # noqa: S608 - names from this file
                    f"WHERE substring({column} from 1 for 4) = :magic"
                ),
                {"magic": b"EIA1"},
            )
        )
    }
    if sealed:
        raise RuntimeError(
            f"sealed values in {sealed}; downgrading would leave them unreadable. Open them "
            "first: einvoice seal --open --apply (with MASTER_KEY set)."
        )
    for table, column, before, _ in COLUMNS:
        text_of = f"convert_from({column}, 'UTF8')"
        op.alter_column(
            table,
            column,
            type_=before,
            postgresql_using=f"({text_of})::jsonb" if before is JSONB else text_of,
        )
    for column, kind, value, _ in INVOICE_COPIES:
        op.add_column("invoices", sa.Column(column, kind, nullable=True))
        op.execute(f"UPDATE invoices SET {column} = {value}")  # noqa: S608
    for column, _, _, nullable in INVOICE_COPIES:
        if not nullable:
            op.alter_column("invoices", column, nullable=False)
    op.create_index(op.f("ix_invoices_number"), "invoices", ["number"], unique=False)
    op.create_index(op.f("ix_invoices_seller_vat_id"), "invoices", ["seller_vat_id"], unique=False)
