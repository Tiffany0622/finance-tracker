"""Append-only item reviews and searchable receipt lines; no ledger changes."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005_items"
down_revision = "0004_capture"
branch_labels = None
depends_on = None


def identity_columns():
    return [
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "receipt_item_reviews",
        *identity_columns(),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("transaction_revision", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(), sa.ForeignKey("currencies.code"), nullable=False),
        sa.Column("parsed_snapshot", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("owner_id", "id"),
        sa.UniqueConstraint("draft_id", "revision"),
        sa.ForeignKeyConstraint(
            ["owner_id", "draft_id"], ["capture_drafts.owner_id", "capture_drafts.id"]
        ),
        sa.CheckConstraint("revision > 0 AND draft_revision > 0"),
        sa.CheckConstraint("transaction_revision IS NULL OR transaction_revision > 0"),
    )
    op.create_table(
        "receipt_items",
        *identity_columns(),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("source_line_no", sa.Integer(), nullable=True),
        sa.Column("raw_name", sa.String(), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=True),
        sa.Column("unit_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("line_total", sa.Numeric(38, 18), nullable=True),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.UniqueConstraint("review_id", "line_no"),
        sa.ForeignKeyConstraint(
            ["owner_id", "review_id"], ["receipt_item_reviews.owner_id", "receipt_item_reviews.id"]
        ),
        sa.CheckConstraint("line_no BETWEEN 1 AND 200"),
        sa.CheckConstraint("source_line_no IS NULL OR source_line_no BETWEEN 1 AND 200"),
        sa.CheckConstraint("quantity IS NULL OR quantity > 0"),
        sa.CheckConstraint("unit_price IS NULL OR unit_price >= 0"),
    )
    op.execute("""CREATE FUNCTION protect_item_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Receipt item history is append-only'; END $$""")
    for table in ("receipt_item_reviews", "receipt_items"):
        op.execute(f"""CREATE TRIGGER immutable_item_history BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION protect_item_history()""")


def downgrade():
    # Disposable databases only. Production rollback requires a verified backup restore.
    op.drop_table("receipt_items")
    op.drop_table("receipt_item_reviews")
    op.execute("DROP FUNCTION protect_item_history()")
