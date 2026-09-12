"""receipt_uploads 回单上传批次（异步解析）+ bank_receipts.file_hash

Revision ID: b7c9d3e5f6a1
Revises: a3b8f2c1d4e5
Create Date: 2026-09-12 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c9d3e5f6a1'
down_revision: Union[str, Sequence[str], None] = 'a3b8f2c1d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('receipt_uploads',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('file_hash', sa.String(length=64), nullable=False),
    sa.Column('file_url', sa.String(length=512), nullable=False),
    sa.Column('file_type', sa.String(length=8), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('receipt_count', sa.Integer(), nullable=False),
    sa.Column('error', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('parsed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('file_hash')
    )
    op.add_column('bank_receipts', sa.Column('file_hash', sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('bank_receipts', 'file_hash')
    op.drop_table('receipt_uploads')
