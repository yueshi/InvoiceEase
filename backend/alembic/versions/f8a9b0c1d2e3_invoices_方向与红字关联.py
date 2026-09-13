"""invoices 方向（进项/销项）+ 红字关联原蓝票

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-09-13 17:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f8a9b0c1d2e3'
down_revision: Union[str, Sequence[str], None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('invoices', sa.Column(
        'invoice_direction', sa.String(length=8), nullable=False, server_default='input'))
    op.add_column('invoices', sa.Column('original_invoice_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('invoices', 'original_invoice_id')
    op.drop_column('invoices', 'invoice_direction')
