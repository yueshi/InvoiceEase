"""expense_claims.dept 部门字段（P0-1 budget check 配套）

Revision ID: d5e6f7a8b9c2
Revises: d5e6f7a8b9c1
Create Date: 2026-10-09 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c2"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "expense_claims",
        sa.Column("dept", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_expense_claims_dept", "expense_claims", ["dept"])
    # 历史数据：保持 NULL（迁移期兼容；P0-1 validate_expense 见到 NULL 会给
    # NO_DEPT warning，不会因缺 dept 直接 FAIL 阻塞历史单）


def downgrade() -> None:
    op.drop_index("ix_expense_claims_dept", table_name="expense_claims")
    op.drop_column("expense_claims", "dept")