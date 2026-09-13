"""audit_logs.outcome 结果类别

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-09-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('audit_logs', sa.Column('outcome', sa.String(length=16), nullable=True))
    op.create_index('ix_audit_logs_outcome', 'audit_logs', ['outcome'])


def downgrade() -> None:
    op.drop_index('ix_audit_logs_outcome', table_name='audit_logs')
    op.drop_column('audit_logs', 'outcome')
