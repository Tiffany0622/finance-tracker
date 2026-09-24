"""receipt attachments and ordered pages"""

import sqlalchemy as sa

from alembic import op

revision = "0003_receipts"
down_revision = "0002_ledger"
branch_labels = None
depends_on = None


def upgrade():
    # Reviewed static migration: additive tables; existing financial rows are untouched.
    op.create_table(
        "attachments",
        sa.Column("storage_key", sa.Uuid(), nullable=False),
        sa.Column("upload_key", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("preview_sha256", sa.String(length=64), nullable=False),
        sa.Column("mime", sa.String(length=40), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("original_name", sa.String(length=200), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("(status = 'deleting') = (deleted_at IS NOT NULL)"),
        sa.CheckConstraint("mime IN ('image/jpeg','image/png','image/heic')"),
        sa.CheckConstraint("purpose = 'receipt'"),
        sa.CheckConstraint("status IN ('ready','deleting')"),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 20971520"),
        sa.CheckConstraint("width > 0 AND height > 0 AND width::bigint * height <= 50000000"),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "id"),
        sa.UniqueConstraint("owner_id", "upload_key"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_table(
        "receipts",
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("parse_status", sa.String(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("parse_status = 'not_requested'"),
        sa.ForeignKeyConstraint(
            ["owner_id", "transaction_id"],
            ["transactions.owner_id", "transactions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "id"),
        sa.UniqueConstraint("owner_id", "transaction_id"),
    )
    op.create_table(
        "transaction_attachments",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("attachment_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.CheckConstraint("role = 'receipt'"),
        sa.ForeignKeyConstraint(
            ["owner_id", "attachment_id"],
            ["attachments.owner_id", "attachments.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "transaction_id"],
            ["transactions.owner_id", "transactions.id"],
        ),
        sa.PrimaryKeyConstraint("owner_id", "transaction_id", "attachment_id"),
    )
    op.create_table(
        "receipt_attachments",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        sa.Column("attachment_id", sa.Uuid(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.CheckConstraint("page_no > 0"),
        sa.ForeignKeyConstraint(
            ["owner_id", "attachment_id"],
            ["attachments.owner_id", "attachments.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "receipt_id"],
            ["receipts.owner_id", "receipts.id"],
        ),
        sa.PrimaryKeyConstraint("owner_id", "receipt_id", "attachment_id"),
        sa.UniqueConstraint("receipt_id", "page_no"),
    )


def downgrade():
    # Reviewed static migration: additive tables; existing financial rows are untouched.
    op.drop_table("receipt_attachments")
    op.drop_table("transaction_attachments")
    op.drop_table("receipts")
    op.drop_table("attachments")
