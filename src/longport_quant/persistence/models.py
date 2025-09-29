"""SQLAlchemy ORM models."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, String, UniqueConstraint
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
