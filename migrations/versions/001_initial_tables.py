"""Create initial tables for longport quant system

Revision ID: 001
Revises:
Create Date: 2025-01-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create watchsymbol table (existing table from base models)
    op.create_table('watchsymbol',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('symbol', sa.String(32), nullable=False),
        sa.Column('market', sa.String(8), nullable=False),
        sa.Column('description', sa.String(128))
    )
    op.create_unique_constraint('uq_symbol_market', 'watchsymbol', ['symbol', 'market'])

    # Create orderrecord table
    op.create_table('orderrecord',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('order_id', sa.String(64), nullable=False, unique=True),
        sa.Column('symbol', sa.String(32), nullable=False),
        sa.Column('side', sa.String(4), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False)
    )

    # Create fillrecord table
    op.create_table('fillrecord',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('order_id', sa.String(64), nullable=False),
        sa.Column('trade_id', sa.String(64), nullable=False),
        sa.Column('symbol', sa.String(32), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('filled_at', sa.DateTime(timezone=True), nullable=False)
    )

    # Create tradingcalendar table
    op.create_table('tradingcalendar',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('market', sa.String(8), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('sessions', postgresql.JSONB(), nullable=False),
        sa.Column('is_half_day', sa.Boolean(), nullable=False, default=False),
        sa.Column('source', sa.String(32), nullable=False, default='longport_api'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP'))
    )
    op.create_unique_constraint('uq_trading_calendar_market_date', 'tradingcalendar', ['market', 'trade_date'])

    # Create kline_daily table with partitioning
    op.execute("""
        CREATE TABLE kline_daily (
            symbol VARCHAR(32) NOT NULL,
            trade_date DATE NOT NULL,
            open DECIMAL(12,4),
            high DECIMAL(12,4),
            low DECIMAL(12,4),
            close DECIMAL(12,4),
            volume BIGINT,
            turnover DECIMAL(18,2),
            prev_close DECIMAL(12,4),
            change_val DECIMAL(12,4),
            change_rate DECIMAL(8,4),
            amplitude DECIMAL(8,4),
            turnover_rate DECIMAL(8,4),
            adjust_flag INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, trade_date)
        ) PARTITION BY RANGE (trade_date);
    """)

    # Create yearly partitions for kline_daily
    op.execute("""
        CREATE TABLE kline_daily_2023 PARTITION OF kline_daily
        FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
    """)
    op.execute("""
        CREATE TABLE kline_daily_2024 PARTITION OF kline_daily
        FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
    """)
    op.execute("""
        CREATE TABLE kline_daily_2025 PARTITION OF kline_daily
        FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
    """)
    op.execute("""
        CREATE TABLE kline_daily_2026 PARTITION OF kline_daily
        FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
    """)

    # Create indexes for kline_daily
    op.create_index('idx_kline_daily_symbol', 'kline_daily', ['symbol'])
    op.create_index('idx_kline_daily_date', 'kline_daily', ['trade_date'])

    # Create kline_minute table with partitioning
    op.execute("""
        CREATE TABLE kline_minute (
            symbol VARCHAR(32) NOT NULL,
            timestamp TIMESTAMP NOT NULL,
            open DECIMAL(12,4),
            high DECIMAL(12,4),
            low DECIMAL(12,4),
            close DECIMAL(12,4),
            volume BIGINT,
            turnover DECIMAL(18,2),
            trade_count INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, timestamp)
        ) PARTITION BY RANGE (timestamp);
    """)

    # Create monthly partitions for kline_minute (recent months)
    op.execute("""
        CREATE TABLE kline_minute_2024_12 PARTITION OF kline_minute
        FOR VALUES FROM ('2024-12-01') TO ('2025-01-01');
    """)
    op.execute("""
        CREATE TABLE kline_minute_2025_01 PARTITION OF kline_minute
        FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
    """)
    op.execute("""
        CREATE TABLE kline_minute_2025_02 PARTITION OF kline_minute
        FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
    """)
    op.execute("""
        CREATE TABLE kline_minute_2025_03 PARTITION OF kline_minute
        FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
    """)
    op.execute("""
        CREATE TABLE kline_minute_2025_04 PARTITION OF kline_minute
        FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
    """)
    op.execute("""
        CREATE TABLE kline_minute_2025_05 PARTITION OF kline_minute
        FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
    """)
    op.execute("""
        CREATE TABLE kline_minute_2025_06 PARTITION OF kline_minute
        FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
    """)

    # Create index for kline_minute
    op.create_index('idx_kline_minute_symbol_time', 'kline_minute', ['symbol', 'timestamp'])

    # Create realtime_quotes table
    op.create_table('realtime_quotes',
        sa.Column('symbol', sa.String(32), nullable=False),
        sa.Column('timestamp', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('last_done', sa.DECIMAL(12, 4)),
        sa.Column('prev_close', sa.DECIMAL(12, 4)),
        sa.Column('open', sa.DECIMAL(12, 4)),
        sa.Column('high', sa.DECIMAL(12, 4)),
        sa.Column('low', sa.DECIMAL(12, 4)),
        sa.Column('volume', sa.BIGINT()),
        sa.Column('turnover', sa.DECIMAL(18, 2)),
        sa.Column('bid_price', sa.DECIMAL(12, 4)),
        sa.Column('ask_price', sa.DECIMAL(12, 4)),
        sa.Column('bid_volume', sa.BIGINT()),
        sa.Column('ask_volume', sa.BIGINT()),
        sa.Column('trade_status', sa.String(16)),
        sa.PrimaryKeyConstraint('symbol', 'timestamp')
    )

    # Create security_static table
    op.create_table('security_static',
        sa.Column('symbol', sa.String(32), nullable=False),
        sa.Column('name_cn', sa.String(128)),
        sa.Column('name_en', sa.String(128)),
        sa.Column('exchange', sa.String(16)),
        sa.Column('currency', sa.String(8)),
        sa.Column('lot_size', sa.Integer()),
        sa.Column('total_shares', sa.BIGINT()),
        sa.Column('circulating_shares', sa.BIGINT()),
        sa.Column('eps', sa.DECIMAL(10, 4)),
        sa.Column('eps_ttm', sa.DECIMAL(10, 4)),
        sa.Column('bps', sa.DECIMAL(10, 4)),
        sa.Column('dividend_yield', sa.DECIMAL(6, 4)),
        sa.Column('board', sa.String(32)),
        sa.Column('updated_at', sa.TIMESTAMP()),
        sa.PrimaryKeyConstraint('symbol')
    )

    # Create calc_indicators table
    op.create_table('calc_indicators',
        sa.Column('symbol', sa.String(32), nullable=False),
        sa.Column('timestamp', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('pe_ttm', sa.DECIMAL(10, 2)),
        sa.Column('pb_ratio', sa.DECIMAL(10, 2)),
        sa.Column('turnover_rate', sa.DECIMAL(6, 4)),
        sa.Column('volume_ratio', sa.DECIMAL(10, 2)),
        sa.Column('amplitude', sa.DECIMAL(6, 2)),
        sa.Column('capital_flow', sa.DECIMAL(18, 2)),
        sa.Column('ytd_change_rate', sa.DECIMAL(8, 4)),
        sa.Column('five_day_change', sa.DECIMAL(8, 4)),
        sa.Column('ten_day_change', sa.DECIMAL(8, 4)),
        sa.Column('half_year_change', sa.DECIMAL(8, 4)),
        sa.PrimaryKeyConstraint('symbol', 'timestamp')
    )

    # Create market_depth table
    op.create_table('market_depth',
        sa.Column('symbol', sa.String(32), nullable=False),
        sa.Column('timestamp', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('price', sa.DECIMAL(12, 4)),
        sa.Column('volume', sa.BIGINT()),
        sa.Column('side', sa.String(4), nullable=False),
        sa.Column('broker_count', sa.Integer()),
        sa.PrimaryKeyConstraint('symbol', 'timestamp', 'side', 'position')
    )

    # Create trading_signals table
    op.create_table('trading_signals',
        sa.Column('id', sa.Integer(), autoincrement=True),
        sa.Column('symbol', sa.String(32), nullable=False),
        sa.Column('strategy_name', sa.String(64)),
        sa.Column('signal_type', sa.String(16)),
        sa.Column('signal_strength', sa.DECIMAL(4, 2)),
        sa.Column('price_target', sa.DECIMAL(12, 4)),
        sa.Column('stop_loss', sa.DECIMAL(12, 4)),
        sa.Column('take_profit', sa.DECIMAL(12, 4)),
        sa.Column('reason', postgresql.JSONB()),
        sa.Column('features', postgresql.JSONB()),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('executed', sa.Boolean(), default=False),
        sa.Column('order_id', sa.String(64)),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_signals_symbol_created', 'trading_signals', ['symbol', 'created_at'])

    # Create positions table
    op.create_table('positions',
        sa.Column('account_id', sa.String(32), nullable=False),
        sa.Column('symbol', sa.String(32), nullable=False),
        sa.Column('quantity', sa.DECIMAL(12, 2)),
        sa.Column('available_quantity', sa.DECIMAL(12, 2)),
        sa.Column('currency', sa.String(8)),
        sa.Column('cost_price', sa.DECIMAL(12, 4)),
        sa.Column('market_value', sa.DECIMAL(18, 2)),
        sa.Column('unrealized_pnl', sa.DECIMAL(18, 2)),
        sa.Column('realized_pnl', sa.DECIMAL(18, 2)),
        sa.Column('updated_at', sa.TIMESTAMP()),
        sa.PrimaryKeyConstraint('account_id', 'symbol')
    )

    # Create strategy_features table
    op.create_table('strategy_features',
        sa.Column('symbol', sa.String(32), nullable=False),
        sa.Column('timestamp', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('feature_name', sa.String(64), nullable=False),
        sa.Column('value', sa.DECIMAL(16, 6)),
        sa.Column('metadata', postgresql.JSONB()),
        sa.PrimaryKeyConstraint('symbol', 'timestamp', 'feature_name')
    )
    op.create_index('idx_features_symbol_feature', 'strategy_features', ['symbol', 'feature_name'])


def downgrade() -> None:
    # Drop all tables in reverse order
    op.drop_table('strategy_features')
    op.drop_table('positions')
    op.drop_table('trading_signals')
    op.drop_table('market_depth')
    op.drop_table('calc_indicators')
    op.drop_table('security_static')
    op.drop_table('realtime_quotes')
    op.drop_table('tradingcalendar')
    op.drop_table('fillrecord')
    op.drop_table('orderrecord')
    op.drop_table('watchsymbol')

    # Drop partitioned tables
    op.execute('DROP TABLE kline_minute CASCADE')
    op.execute('DROP TABLE kline_daily CASCADE')