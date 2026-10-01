"""agent_messages.blocks 助手消息时间线（思考/工具/文本有序块）

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_messages") as b:
        b.add_column(sa.Column("blocks", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_messages") as b:
        b.drop_column("blocks")
