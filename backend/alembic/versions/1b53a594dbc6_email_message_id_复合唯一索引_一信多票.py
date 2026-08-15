"""email_message_id 复合唯一索引（一信多票）

Revision ID: 1b53a594dbc6
Revises: 42982028ec09
Create Date: 2026-08-15 09:16:07.961205

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1b53a594dbc6'
down_revision: Union[str, Sequence[str], None] = '42982028ec09'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index('uq_invoices_email_message_id', table_name='invoices')
    op.create_index(
        'uq_invoices_email_message_id',
        'invoices',
        ['email_message_id', 'file_url'],
        unique=True,
        postgresql_where=sa.text('email_message_id IS NOT NULL'),
        sqlite_where=sa.text('email_message_id IS NOT NULL'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_invoices_email_message_id', table_name='invoices')
    op.create_index(
        'uq_invoices_email_message_id',
        'invoices',
        ['email_message_id'],
        unique=True,
        postgresql_where=sa.text('email_message_id IS NOT NULL'),
        sqlite_where=sa.text('email_message_id IS NOT NULL'),
    )
