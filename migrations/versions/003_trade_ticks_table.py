"""Add trade_ticks table for storing trade tick data.

Revision ID: 003
Revises: 002
Create Date: 2025-09-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade():
    """Create trade_ticks table."""

    # Create trade_ticks table
    op.create_table(
        'trade_ticks',
        sa.Column('id', sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column('symbol', sa.String(32), nullable=False),
        sa.Column('price', sa.DECIMAL(12, 4)),
        sa.Column('volume', sa.BIGINT()),
        sa.Column('timestamp', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('direction', sa.String(16)),  # buy/sell/neutral
        sa.Column('trade_type', sa.String(16)),  # auto/manual
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes
    op.create_index('ix_trade_ticks_symbol', 'trade_ticks', ['symbol'])
    op.create_index('ix_trade_ticks_timestamp', 'trade_ticks', ['timestamp'])
    op.create_index('ix_trade_ticks_symbol_timestamp', 'trade_ticks', ['symbol', 'timestamp'])


def downgrade():
    """Drop trade_ticks table."""
    op.drop_index('ix_trade_ticks_symbol_timestamp', 'trade_ticks')
    op.drop_index('ix_trade_ticks_timestamp', 'trade_ticks')
    op.drop_index('ix_trade_ticks_symbol', 'trade_ticks')
    op.drop_table('trade_ticks')