"""bank_receipts 质量字段/方向 + company_infos.bank_account

Revision ID: c8d1e4f6a2b3
Revises: b7c9d3e5f6a1
Create Date: 2026-09-12 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8d1e4f6a2b3'
down_revision: Union[str, Sequence[str], None] = 'b7c9d3e5f6a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('bank_receipts', sa.Column('direction', sa.String(length=8), nullable=True))
    op.add_column('bank_receipts', sa.Column('needs_review', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('bank_receipts', sa.Column('quality_issues', sa.JSON(), nullable=True))
    op.add_column('company_infos', sa.Column('bank_account', sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('company_infos', 'bank_account')
    op.drop_column('bank_receipts', 'quality_issues')
    op.drop_column('bank_receipts', 'needs_review')
    op.drop_column('bank_receipts', 'direction')
