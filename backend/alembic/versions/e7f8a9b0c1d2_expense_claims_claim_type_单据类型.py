"""expense_claims.claim_type 单据类型（含存量按事项类型回填）

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-09-13 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, Sequence[str], None] = 'd6e7f8a9b0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'expense_claims',
        sa.Column('claim_type', sa.String(length=32), nullable=False, server_default='other'),
    )
    # 存量回填：取该单第一个事项的类型；无事项则 other
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE expense_claims SET claim_type = COALESCE(("
        "  SELECT entry_type FROM expense_entries WHERE claim_id = expense_claims.id "
        "  ORDER BY id LIMIT 1), 'other')"
    ))


def downgrade() -> None:
    op.drop_column('expense_claims', 'claim_type')
