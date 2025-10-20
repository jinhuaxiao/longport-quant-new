"""SQLAlchemy ORM models."""

from __future__ import annotations

from sqlalchemy import (
    BIGINT,
    Boolean,
    Column,
    DECIMAL,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    TIMESTAMP,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import DeclarativeBase, declared_attr


class Base(DeclarativeBase):
    @declared_attr.directive
    def __tablename__(cls) -> str:  # type: ignore[override]
        return cls.__name__.lower()


class WatchSymbol(Base):
    id = Column(Integer, primary_key=True)
    symbol = Column(String(32), nullable=False)
    market = Column(String(8), nullable=False)
    description = Column(String(128))

    __table_args__ = (UniqueConstraint("symbol", "market", name="uq_symbol_market"),)


class OrderRecord(Base):
    id = Column(Integer, primary_key=True)
    order_id = Column(String(64), nullable=False, unique=True)
    symbol = Column(String(32), nullable=False)
    side = Column(String(4), nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    status = Column(String(16), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class FillRecord(Base):
    id = Column(Integer, primary_key=True)
    order_id = Column(String(64), nullable=False)
    trade_id = Column(String(64), nullable=False)
    symbol = Column(String(32), nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    filled_at = Column(DateTime(timezone=True), nullable=False)


class SecurityUniverse(Base):
    __tablename__ = "security_universe"

    symbol = Column(String(32), primary_key=True)
    market = Column(String(8), nullable=False)
    name_cn = Column(String(128))
    name_en = Column(String(128))
    name_hk = Column(String(128))
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TradingCalendar(Base):
    id = Column(Integer, primary_key=True)
    market = Column(String(8), nullable=False)
    trade_date = Column(Date, nullable=False)
    sessions = Column(JSONB, nullable=False)
    is_half_day = Column(Boolean, nullable=False, default=False)
    source = Column(String(32), nullable=False, default="longport_api")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("market", "trade_date", name="uq_trading_calendar_market_date"),
    )


class KlineDaily(Base):
    __tablename__ = "kline_daily"

    symbol = Column(String(32), primary_key=True)
    trade_date = Column(Date, primary_key=True)
    open = Column(DECIMAL(12, 4))
    high = Column(DECIMAL(12, 4))
    low = Column(DECIMAL(12, 4))
    close = Column(DECIMAL(12, 4))
    volume = Column(BIGINT)
    turnover = Column(DECIMAL(18, 2))
    prev_close = Column(DECIMAL(12, 4))
    change_val = Column(DECIMAL(12, 4))
    change_rate = Column(DECIMAL(8, 4))
    amplitude = Column(DECIMAL(8, 4))
    turnover_rate = Column(DECIMAL(8, 4))
    adjust_flag = Column(Integer)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class KlineMinute(Base):
    __tablename__ = "kline_minute"

    symbol = Column(String(32), primary_key=True)
    timestamp = Column(TIMESTAMP, primary_key=True)
    open = Column(DECIMAL(12, 4))
    high = Column(DECIMAL(12, 4))
    low = Column(DECIMAL(12, 4))
    close = Column(DECIMAL(12, 4))
    volume = Column(BIGINT)
    turnover = Column(DECIMAL(18, 2))
    trade_count = Column(Integer)
    created_at = Column(TIMESTAMP, server_default=func.now())


class RealtimeQuote(Base):
    __tablename__ = "realtime_quotes"

    symbol = Column(String(32), primary_key=True)
    timestamp = Column(TIMESTAMP(timezone=True), primary_key=True)
    last_done = Column(DECIMAL(12, 4))
    prev_close = Column(DECIMAL(12, 4))
    open = Column(DECIMAL(12, 4))
    high = Column(DECIMAL(12, 4))
    low = Column(DECIMAL(12, 4))
    volume = Column(BIGINT)
    turnover = Column(DECIMAL(18, 2))
    bid_price = Column(DECIMAL(12, 4))
    ask_price = Column(DECIMAL(12, 4))
    bid_volume = Column(BIGINT)
    ask_volume = Column(BIGINT)
    trade_status = Column(String(16))


class SecurityStatic(Base):
    __tablename__ = "security_static"

    symbol = Column(String(32), primary_key=True)
    name_cn = Column(String(128))
    name_en = Column(String(128))
    exchange = Column(String(16))
    currency = Column(String(8))
    lot_size = Column(Integer)
    total_shares = Column(BIGINT)
    circulating_shares = Column(BIGINT)
    eps = Column(DECIMAL(10, 4))
    eps_ttm = Column(DECIMAL(10, 4))
    bps = Column(DECIMAL(10, 4))
    dividend_yield = Column(DECIMAL(6, 4))
    board = Column(String(32))
    updated_at = Column(TIMESTAMP)


class CalcIndicator(Base):
    __tablename__ = "calc_indicators"

    symbol = Column(String(32), primary_key=True)
    timestamp = Column(TIMESTAMP(timezone=True), primary_key=True)
    pe_ttm = Column(DECIMAL(10, 2))
    pb_ratio = Column(DECIMAL(10, 2))
    turnover_rate = Column(DECIMAL(6, 4))
    volume_ratio = Column(DECIMAL(10, 2))
    amplitude = Column(DECIMAL(6, 2))
    capital_flow = Column(DECIMAL(18, 2))
    ytd_change_rate = Column(DECIMAL(8, 4))
    five_day_change = Column(DECIMAL(8, 4))
    ten_day_change = Column(DECIMAL(8, 4))
    half_year_change = Column(DECIMAL(8, 4))


class MarketDepth(Base):
    __tablename__ = "market_depth"

    symbol = Column(String(32), primary_key=True)
    timestamp = Column(TIMESTAMP(timezone=True), primary_key=True)
    position = Column(Integer, primary_key=True)
    side = Column(String(4), primary_key=True)
    price = Column(DECIMAL(12, 4))
    volume = Column(BIGINT)
    broker_count = Column(Integer)


class TradingSignal(Base):
    __tablename__ = "trading_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False)
    strategy_name = Column(String(64))
    signal_type = Column(String(16))
    signal_strength = Column(DECIMAL(4, 2))
    price_target = Column(DECIMAL(12, 4))
    stop_loss = Column(DECIMAL(12, 4))
    take_profit = Column(DECIMAL(12, 4))
    reason = Column(JSONB)
    features = Column(JSONB)
    created_at = Column(TIMESTAMP, server_default=func.now())
    executed = Column(Boolean, default=False)
    order_id = Column(String(64))


class Position(Base):
    __tablename__ = "positions"

    account_id = Column(String(32), primary_key=True)
    symbol = Column(String(32), primary_key=True)
    quantity = Column(DECIMAL(12, 2))
    available_quantity = Column(DECIMAL(12, 2))
    currency = Column(String(8))
    cost_price = Column(DECIMAL(12, 4))
    market_value = Column(DECIMAL(18, 2))
    unrealized_pnl = Column(DECIMAL(18, 2))
    realized_pnl = Column(DECIMAL(18, 2))
    updated_at = Column(TIMESTAMP)


class StrategyFeature(Base):
    __tablename__ = "strategy_features"

    symbol = Column(String(32), primary_key=True)
    timestamp = Column(TIMESTAMP(timezone=True), primary_key=True)
    feature_name = Column(String(64), primary_key=True)
    value = Column(DECIMAL(16, 6))
    meta_data = Column(JSONB, name="metadata")  # Rename to avoid conflict with SQLAlchemy's metadata


class TradeTick(Base):
    __tablename__ = "trade_ticks"

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False, index=True)
    price = Column(DECIMAL(12, 4))
    volume = Column(BIGINT)
    timestamp = Column(TIMESTAMP(timezone=True), nullable=False, index=True)
    direction = Column(String(16))  # buy/sell/neutral
    trade_type = Column(String(16))  # auto/manual
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
