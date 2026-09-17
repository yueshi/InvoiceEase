"""bank_receipts 交易性质分类（回单分类体系）

Revision ID: b8c9d0e1f2a3
Revises: d1e2f3a4b5c6
Create Date: 2026-09-17
"""
from alembic import op
import sqlalchemy as sa

revision = "b8c9d0e1f2a3"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bank_receipts") as batch:
        batch.add_column(sa.Column("category", sa.String(length=24), nullable=False, server_default="unknown"))
        batch.add_column(sa.Column("category_source", sa.String(length=8), nullable=False, server_default="rule"))
    op.create_index("ix_bank_receipts_category", "bank_receipts", ["category"])


def downgrade() -> None:
    op.drop_index("ix_bank_receipts_category", table_name="bank_receipts")
    with op.batch_alter_table("bank_receipts") as batch:
        batch.drop_column("category_source")
        batch.drop_column("category")
