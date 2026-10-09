"""budgets 表（部门×类别×月三维；P0-1 validate_expense 配套）

Revision ID: d5e6f7a8b9c1
Revises: d5e6f7a8b9c0
Create Date: 2026-10-09 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c1"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dept", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("note", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "dept", "category", "period", name="uq_budget_dim"
        ),
    )
    op.create_index("ix_budgets_tenant_id", "budgets", ["tenant_id"])
    op.create_index("ix_budgets_dept", "budgets", ["dept"])
    op.create_index("ix_budgets_category", "budgets", ["category"])
    op.create_index("ix_budgets_period", "budgets", ["period"])


def downgrade() -> None:
    op.drop_index("ix_budgets_period", table_name="budgets")
    op.drop_index("ix_budgets_category", table_name="budgets")
    op.drop_index("ix_budgets_dept", table_name="budgets")
    op.drop_index("ix_budgets_tenant_id", table_name="budgets")
    op.drop_table("budgets")