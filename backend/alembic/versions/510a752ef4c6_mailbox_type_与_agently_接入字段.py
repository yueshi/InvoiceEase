"""mailbox_type 与 agently 接入字段

Revision ID: 510a752ef4c6
Revises: 1b53a594dbc6
Create Date: 2026-08-15 19:43:00.957566

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '510a752ef4c6'
down_revision: Union[str, Sequence[str], None] = '1b53a594dbc6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 新增三列：mailbox_type（server_default='imap' 兼容既有存量行）、agently_workspace、agently_token_encrypted
    op.add_column('mailboxes', sa.Column('mailbox_type', sa.String(length=16), nullable=False, server_default='imap'))
    op.add_column('mailboxes', sa.Column('agently_workspace', sa.String(length=64), nullable=True))
    op.add_column('mailboxes', sa.Column('agently_token_encrypted', sa.String(length=512), nullable=True))
    # SQLite 不支持 ALTER COLUMN DROP NOT NULL → 用批量重建模式放宽 imap 三字段为可空
    with op.batch_alter_table("mailboxes") as batch:
        batch.alter_column("imap_host", existing_type=sa.String(256), nullable=True)
        batch.alter_column("username", existing_type=sa.String(256), nullable=True)
        batch.alter_column("password_encrypted", existing_type=sa.String(512), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    # 反向：nullable=False（若库内有 NULL 值会失败——降级在开发环境执行，仅回退测试数据）
    with op.batch_alter_table("mailboxes") as batch:
        batch.alter_column("imap_host", existing_type=sa.String(256), nullable=False)
        batch.alter_column("username", existing_type=sa.String(256), nullable=False)
        batch.alter_column("password_encrypted", existing_type=sa.String(512), nullable=False)
    op.drop_column('mailboxes', 'agently_token_encrypted')
    op.drop_column('mailboxes', 'agently_workspace')
    op.drop_column('mailboxes', 'mailbox_type')
