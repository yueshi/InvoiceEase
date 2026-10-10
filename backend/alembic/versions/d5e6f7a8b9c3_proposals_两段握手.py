"""proposals 表（P0-2 两段握手，v1.1 §7.5）

Revision ID: d5e6f7a8b9c3
Revises: d5e6f7a8b9c2
Create Date: 2026-10-10 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c3"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "proposals",
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("preview", sa.JSON(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False,
                  server_default="user"),
        sa.Column("channel", sa.String(length=16), nullable=False,
                  server_default="mcp"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(),
                  nullable=False),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index("ix_proposals_tool_name", "proposals", ["tool_name"])
    op.create_index("ix_proposals_expires_at", "proposals", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_proposals_expires_at", table_name="proposals")
    op.drop_index("ix_proposals_tool_name", table_name="proposals")
    op.drop_table("proposals")