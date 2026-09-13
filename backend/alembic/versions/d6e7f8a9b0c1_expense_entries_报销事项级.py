"""expense_entries 报销事项（P0.5）+ expense_items.entry_id（含历史数据归组）

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-09-13 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import invoicing.models.fields  # Money 类型


revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, Sequence[str], None] = 'c5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ENTRY_TYPE_LABELS = {
    "travel": "差旅", "procurement": "采购", "entertainment": "招待",
    "office": "办公", "other": "其他",
}


def upgrade() -> None:
    """幂等：容忍表/列已存在（SQLite 无事务 DDL，半完成状态可重入）。

    注意：SQLite 不支持 ALTER 增加外键约束，故 entry_id 只加列不加 FK；
    模型层声明了 FK（create_all 的测试库会建），应用层负责级联（删事项同时删其凭证）。
    """
    insp = sa.inspect(op.get_bind())
    if 'expense_entries' not in insp.get_table_names():
        _create_entries_table()
    if 'entry_id' not in [c['name'] for c in insp.get_columns('expense_items')]:
        op.add_column('expense_items', sa.Column('entry_id', sa.Integer(), nullable=True))
    _regroup_existing_items(op.get_bind())


def _create_entries_table() -> None:
    op.create_table('expense_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('claim_id', sa.Integer(), nullable=False),
    sa.Column('entry_type', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=256), nullable=False),
    sa.Column('occurred_on', sa.Date(), nullable=True),
    sa.Column('scene_fields', sa.JSON(), nullable=True),
    sa.Column('amount', invoicing.models.fields.Money(precision=14, scale=2), nullable=False),
    sa.Column('note', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['claim_id'], ['expense_claims.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def _regroup_existing_items(conn) -> None:
    """历史数据归组：按 (claim_id, expense_type) 生成默认事项，凭证挂入。"""
    rows = conn.execute(sa.text(
        "SELECT DISTINCT claim_id, expense_type FROM expense_items WHERE entry_id IS NULL"
    )).fetchall()
    for claim_id, expense_type in rows:
        label = _ENTRY_TYPE_LABELS.get(expense_type or "other", "其他")
        result = conn.execute(sa.text(
            "INSERT INTO expense_entries (claim_id, entry_type, title, amount, note, created_at) "
            "VALUES (:claim_id, :entry_type, :title, 0, :note, CURRENT_TIMESTAMP)"
        ), {"claim_id": claim_id, "entry_type": expense_type or "other",
            "title": f"{label}（迁移生成）", "note": "由 P0 明细自动归组"})
        entry_id = result.lastrowid
        conn.execute(sa.text(
            "UPDATE expense_items SET entry_id = :entry_id "
            "WHERE claim_id = :claim_id AND expense_type = :expense_type AND entry_id IS NULL"
        ), {"entry_id": entry_id, "claim_id": claim_id, "expense_type": expense_type})
        conn.execute(sa.text(
            "UPDATE expense_entries SET amount = COALESCE(("
            "  SELECT SUM(amount) FROM expense_items WHERE entry_id = :entry_id AND active = 1), 0) "
            "WHERE id = :entry_id"
        ), {"entry_id": entry_id})


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'entry_id' in [c['name'] for c in insp.get_columns('expense_items')]:
        op.drop_column('expense_items', 'entry_id')
    if 'expense_entries' in insp.get_table_names():
        op.drop_table('expense_entries')
