"""Deposits and withdrawals per trading account.

Revision ID: 0004_cash_transactions
Revises: 0003_import_batches
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0004_cash_transactions'
down_revision: str | None = '0003_import_batches'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

cash_transaction_type = sa.Enum('DEPOSIT', 'WITHDRAWAL', name='cashtransactiontype')


def upgrade() -> None:
    op.create_table('cash_transactions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('account_id', sa.String(length=36), nullable=False),
    sa.Column('type', cash_transaction_type, nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('occurred_on', sa.Date(), nullable=False),
    sa.Column('note', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_cash_transactions_account_date', 'cash_transactions', ['account_id', 'occurred_on'], unique=False)
    op.create_index(op.f('ix_cash_transactions_user_id'), 'cash_transactions', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_cash_transactions_user_id'), table_name='cash_transactions')
    op.drop_index('ix_cash_transactions_account_date', table_name='cash_transactions')
    op.drop_table('cash_transactions')
    cash_transaction_type.drop(op.get_bind(), checkfirst=True)
