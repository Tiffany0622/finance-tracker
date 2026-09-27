"""Owner-scoped product aliases and explicit reviewed-item links."""

import sqlalchemy as sa

from alembic import op

revision = "0006_products"
down_revision = "0005_items"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "products",
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("owner_id", "id"),
        sa.CheckConstraint("revision > 0"),
    )
    op.create_table(
        "product_aliases",
        sa.Column("owner_id", sa.Uuid(), primary_key=True),
        sa.Column("normalized_name", sa.String(200), primary_key=True),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(["owner_id", "product_id"], ["products.owner_id", "products.id"]),
    )
    op.create_index("ix_product_aliases_product_id", "product_aliases", ["product_id"])
    op.add_column("receipt_items", sa.Column("product_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_receipt_items_product",
        "receipt_items",
        "products",
        ["owner_id", "product_id"],
        ["owner_id", "id"],
    )


def downgrade():
    # Disposable databases only; existing item history remains untouched.
    op.drop_constraint("fk_receipt_items_product", "receipt_items", type_="foreignkey")
    op.drop_column("receipt_items", "product_id")
    op.drop_table("product_aliases")
    op.drop_table("products")
