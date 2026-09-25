"""capture drafts and durable telegram inbox"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_capture"
down_revision = "0003_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("receipts_parse_status_check", "receipts", type_="check")
    op.create_check_constraint(
        "receipts_parse_status_check",
        "receipts",
        "parse_status IN ('not_requested','processing','parsed','failed')",
    )
    # Reviewed additive migration; no posted financial rows are changed.
    op.create_table(
        "telegram_cursors",
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("next_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("bot_id"),
    )
    op.create_table(
        "capture_bridges",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("owner_id"),
    )
    op.create_table(
        "telegram_events",
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bot_id", "update_id"),
    )
    op.create_table(
        "capture_drafts",
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_key", sa.String(length=120), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("source_text", sa.String(), nullable=False),
        sa.Column("proposal", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parsed", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("confirmed_transaction_id", sa.Uuid(), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("(status = 'confirmed') = (confirmed_transaction_id IS NOT NULL)"),
        sa.CheckConstraint("source IN ('web','telegram')"),
        sa.CheckConstraint(
            "status IN ('needs_review','processing','confirmed','cancelled','failed')"
        ),
        sa.CheckConstraint("revision > 0"),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "confirmed_transaction_id"],
            ["transactions.owner_id", "transactions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "receipt_id"],
            ["receipts.owner_id", "receipts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "id"),
        sa.UniqueConstraint("owner_id", "source_key"),
        sa.UniqueConstraint("receipt_id"),
    )
    op.create_table(
        "receipt_parse_attempts",
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "draft_id"],
            ["capture_drafts.owner_id", "capture_drafts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "attempt_no"),
    )
    op.alter_column("receipts", "transaction_id", existing_type=sa.UUID(), nullable=True)


def downgrade():
    # Disposable DB only: a populated capture ledger requires restore, not downgrade.
    op.drop_constraint("receipts_parse_status_check", "receipts", type_="check")
    op.execute("UPDATE receipts SET parse_status = 'not_requested'")
    op.create_check_constraint(
        "receipts_parse_status_check", "receipts", "parse_status = 'not_requested'"
    )
    # Reviewed additive migration; no posted financial rows are changed.
    op.alter_column("receipts", "transaction_id", existing_type=sa.UUID(), nullable=False)
    op.drop_table("receipt_parse_attempts")
    op.drop_table("capture_drafts")
    op.drop_table("telegram_events")
    op.drop_table("capture_bridges")
    op.drop_table("telegram_cursors")
