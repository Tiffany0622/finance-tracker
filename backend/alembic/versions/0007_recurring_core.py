"""Persistent recurring calendar cursor and immutable occurrence snapshots."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007_recurring"
down_revision = "0006_products"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "recurring_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("template", postgresql.JSONB(), nullable=False),
        sa.Column("frequency", sa.String(), nullable=False),
        sa.Column("interval", sa.Integer(), nullable=False),
        sa.Column("anchor_date", sa.Date(), nullable=False),
        sa.Column("local_time", sa.String(8), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("end_on", sa.Date()),
        sa.Column("next_local_date", sa.Date()),
        sa.Column("next_due_at", sa.DateTime(timezone=True)),
        sa.Column("posting_mode", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("owner_id", "id"),
        sa.CheckConstraint("frequency IN ('daily','weekly','monthly')"),
        sa.CheckConstraint("interval BETWEEN 1 AND 120"),
        sa.CheckConstraint("posting_mode IN ('auto_post','expect_only')"),
        sa.CheckConstraint("revision > 0"),
        sa.CheckConstraint("end_on IS NULL OR end_on >= anchor_date"),
    )
    op.create_index("ix_recurring_rules_due", "recurring_rules", ["enabled", "next_due_at"])
    op.create_table(
        "recurring_occurrences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("scheduled_local_date", sa.Date(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_amount", sa.Numeric(32, 18), nullable=False),
        sa.Column("currency", sa.String(12), sa.ForeignKey("currencies.code"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("transaction_id", sa.Uuid()),
        sa.Column("actual_verified_at", sa.DateTime(timezone=True)),
        sa.Column("rule_revision", sa.Integer(), nullable=False),
        sa.Column("template", postgresql.JSONB(), nullable=False),
        sa.Column("posting_mode", sa.String(), nullable=False),
        sa.UniqueConstraint("rule_id", "scheduled_local_date"),
        sa.UniqueConstraint("transaction_id"),
        sa.ForeignKeyConstraint(
            ["owner_id", "rule_id"], ["recurring_rules.owner_id", "recurring_rules.id"]
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "transaction_id"], ["transactions.owner_id", "transactions.id"]
        ),
        sa.CheckConstraint("status IN ('expected','posted','matched','skipped')"),
        sa.CheckConstraint("rule_revision > 0"),
        sa.CheckConstraint("posting_mode IN ('auto_post','expect_only')"),
    )


def downgrade():
    # Disposable databases only.
    op.drop_table("recurring_occurrences")
    op.drop_table("recurring_rules")
