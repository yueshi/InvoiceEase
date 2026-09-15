"""users：账号状态（暂停/恢复）与强制改密标记

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-15 15:00:00.000000

两列都是 NOT NULL + server_default：SQLite 给有数据的表加非空列必须带默认值，
否则存量行无法回填（本仓既有约定）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('status', sa.String(length=16), nullable=False, server_default='active'),
    )
    op.add_column(
        'users',
        sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('users', 'must_change_password')
    op.drop_column('users', 'status')
