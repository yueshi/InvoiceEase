"""bank_receipts 原件定位：page_no + anchor

Revision ID: d9e2f5a7b3c4
Revises: c8d1e4f6a2b3
Create Date: 2026-09-12 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e2f5a7b3c4'
down_revision: Union[str, Sequence[str], None] = 'c8d1e4f6a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('bank_receipts', sa.Column('page_no', sa.Integer(), nullable=True))
    op.add_column('bank_receipts', sa.Column('anchor', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('bank_receipts', 'anchor')
    op.drop_column('bank_receipts', 'page_no')
