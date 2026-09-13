"""expense_items.auto_rule 自动凭证标识（差旅伙食补助等派生凭证）

Revisions: f8a9b0c1d2e3 -> a1b2c3d4e5f6
Create Date: 2026-09-13 18:00:00.000000

可空列，SQLite 直接 add_column 即可（无需 server_default）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f8a9b0c1d2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('expense_items', sa.Column('auto_rule', sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column('expense_items', 'auto_rule')
