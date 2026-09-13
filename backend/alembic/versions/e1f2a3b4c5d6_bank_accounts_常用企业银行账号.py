"""bank_accounts 常用企业银行账号（含 company_infos.bank_account 数据迁移）

Revision ID: e1f2a3b4c5d6
Revises: d9e2f5a7b3c4
Create Date: 2026-09-13 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd9e2f5a7b3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('bank_accounts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('account_no', sa.String(length=64), nullable=False),
    sa.Column('account_name', sa.String(length=256), nullable=True),
    sa.Column('bank_name', sa.String(length=128), nullable=True),
    sa.Column('remark', sa.String(length=256), nullable=True),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_no')
    )
    # 数据迁移：company_infos.bank_account（kind=self）非空值迁入新表
    op.execute(
        """
        INSERT INTO bank_accounts
            (tenant_id, account_no, account_name, remark, is_default, enabled, created_at, updated_at)
        SELECT 'default', TRIM(bank_account), name, '由公司字典迁移', is_default, 1,
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM company_infos
        WHERE bank_account IS NOT NULL AND TRIM(bank_account) != ''
          AND TRIM(bank_account) NOT IN (SELECT account_no FROM bank_accounts)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('bank_accounts')
