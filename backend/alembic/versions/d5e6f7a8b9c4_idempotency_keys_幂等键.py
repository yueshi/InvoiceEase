"""idempotency_keys 表（P0-2 幂等键，v1.1 §7.5）

Revision ID: d5e6f7a8b9c4
Revises: d5e6f7a8b9c3
Create Date: 2026-10-10 09:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5e6f7a8b9c4"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key", "tool_name"),
    )
    op.create_index("ix_idempotency_keys_expires_at", "idempotency_keys",
                    ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_expires_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")