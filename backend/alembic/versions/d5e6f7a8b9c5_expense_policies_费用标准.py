"""expense_policies 表（P1 费用标准政策，v1.1 §4.2/§4.3）

Revision ID: d5e6f7a8b9c5
Revises: d5e6f7a8b9c4
Create Date: 2026-10-10 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5e6f7a8b9c5"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "expense_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("item_key", sa.String(length=48), nullable=False),
        sa.Column("city_tier", sa.String(length=32), nullable=False,
                  server_default="default"),
        sa.Column("standard", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("tolerance", sa.Numeric(precision=18, scale=2), nullable=False,
                  server_default="0"),
        sa.Column("unit", sa.String(length=16), nullable=False,
                  server_default="per_day"),
        sa.Column("note", sa.String(length=256), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(),
                  nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "category", "item_key", "city_tier",
                            name="uq_policy_dim"),
    )
    op.create_index("ix_expense_policies_tenant_id", "expense_policies", ["tenant_id"])
    op.create_index("ix_expense_policies_category", "expense_policies", ["category"])
    op.create_index("ix_expense_policies_item_key", "expense_policies", ["item_key"])
    op.create_index("ix_expense_policies_city_tier", "expense_policies", ["city_tier"])


def downgrade() -> None:
    op.drop_index("ix_expense_policies_city_tier", table_name="expense_policies")
    op.drop_index("ix_expense_policies_item_key", table_name="expense_policies")
    op.drop_index("ix_expense_policies_category", table_name="expense_policies")
    op.drop_index("ix_expense_policies_tenant_id", table_name="expense_policies")
    op.drop_table("expense_policies")