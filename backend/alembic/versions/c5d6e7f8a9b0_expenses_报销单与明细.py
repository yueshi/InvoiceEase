"""expense_claims / expense_items 报销单与明细 + invoices.reimbursement_status

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-09-13 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import invoicing.models.fields  # Money 类型（autogenerate 不自动加）


revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, Sequence[str], None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('expense_claims',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('claim_no', sa.String(length=32), nullable=False),
    sa.Column('applicant_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=256), nullable=False),
    sa.Column('total_amount', invoicing.models.fields.Money(precision=14, scale=2), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('approver_id', sa.Integer(), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rejected_reason', sa.String(length=512), nullable=True),
    sa.Column('remark', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['applicant_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['approver_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('claim_no')
    )
    op.create_table('expense_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('claim_id', sa.Integer(), nullable=False),
    sa.Column('invoice_id', sa.Integer(), nullable=True),
    sa.Column('receipt_id', sa.Integer(), nullable=True),
    sa.Column('voucher_type', sa.String(length=24), nullable=False),
    sa.Column('amount', invoicing.models.fields.Money(precision=14, scale=2), nullable=False),
    sa.Column('expense_type', sa.String(length=32), nullable=False),
    sa.Column('note', sa.String(length=512), nullable=True),
    sa.Column('payee_name', sa.String(length=128), nullable=True),
    sa.Column('payee_id_no', sa.String(length=32), nullable=True),
    sa.Column('attachment_url', sa.String(length=512), nullable=True),
    sa.Column('deductible', sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column('deductible_note', sa.String(length=256), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['claim_id'], ['expense_claims.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['receipt_id'], ['bank_receipts.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_expense_item_active_invoice', 'expense_items', ['invoice_id'], unique=True,
                    sqlite_where=sa.text("active = 1 AND invoice_id IS NOT NULL"),
                    postgresql_where=sa.text("active AND invoice_id IS NOT NULL"))
    op.create_index('uq_expense_item_active_receipt', 'expense_items', ['receipt_id'], unique=True,
                    sqlite_where=sa.text("active = 1 AND receipt_id IS NOT NULL"),
                    postgresql_where=sa.text("active AND receipt_id IS NOT NULL"))
    op.add_column('invoices', sa.Column('reimbursement_status', sa.String(length=16),
                                        nullable=False, server_default='none'))


def downgrade() -> None:
    op.drop_column('invoices', 'reimbursement_status')
    op.drop_index('uq_expense_item_active_receipt', table_name='expense_items')
    op.drop_index('uq_expense_item_active_invoice', table_name='expense_items')
    op.drop_table('expense_items')
    op.drop_table('expense_claims')
