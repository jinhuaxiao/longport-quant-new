"""Enhanced portfolio state management service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime, date, timedelta
from decimal import Decimal
from enum import Enum
import asyncio

from loguru import logger
import pandas as pd
import numpy as np

from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import Position, OrderRecord, FillRecord, KlineDaily
from longport.openapi import TradeContext
from sqlalchemy import select, and_, update, delete, func
from sqlalchemy.dialects.postgresql import insert


class PositionStatus(Enum):
    """Position status."""
    OPEN = "open"
    CLOSED = "closed"
    PARTIAL = "partial"


@dataclass
class PositionInfo:
    """Detailed position information."""
    symbol: str
    quantity: float
    cost_price: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float
    pnl_percent: float
    position_type: str  # LONG or SHORT
    opened_at: datetime
    updated_at: datetime
    account_id: Optional[str] = None
    currency: str = "HKD"


@dataclass
class PortfolioSnapshot:
    """Portfolio snapshot at a point in time."""
    timestamp: datetime
    account_id: str
    cash: float
    total_value: float
    positions: Dict[str, PositionInfo]
    long_exposure: float
    short_exposure: float
    gross_exposure: float
    net_exposure: float
    position_count: int
    currency: str = "HKD"


@dataclass
class PortfolioMetrics:
    """Portfolio performance metrics."""
    total_return: float
    daily_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    total_trades: int
    winning_trades: int
    losing_trades: int


class PortfolioService:
    """Enhanced portfolio management service."""

    def __init__(
        self,
        db_manager: DatabaseSessionManager,
        trade_context: Optional[TradeContext] = None,
        account_id: str = "default"
    ):
        """
        Initialize portfolio service.

        Args:
            db_manager: Database manager
            trade_context: LongPort trade context for real-time data
            account_id: Account identifier
        """
        self._db = db_manager
        self._trade_context = trade_context
        self._account_id = account_id
        self._cache: Optional[PortfolioSnapshot] = None
        self._positions: Dict[str, PositionInfo] = {}
        self._cash_balance: float = 0.0
        self._last_refresh: Optional[datetime] = None

    async def initialize(self, initial_cash: float = 1000000.0):
        """
        Initialize portfolio with starting capital.

        Args:
            initial_cash: Initial cash balance
        """
        self._cash_balance = initial_cash
        await self._load_positions()
        await self.refresh()
        logger.info(f"Portfolio initialized with cash: {initial_cash:,.2f}")

    async def refresh(self) -> PortfolioSnapshot:
        """
        Refresh portfolio state from database and broker.

        Returns:
            Current portfolio snapshot
        """
        logger.debug("Refreshing portfolio state")

        # Load positions from database
        await self._load_positions()

        # Sync with broker if available
        if self._trade_context:
            await self._sync_with_broker()

        # Update market prices
        await self._update_market_prices()

        # Calculate metrics
        snapshot = await self._calculate_snapshot()
        self._cache = snapshot
        self._last_refresh = datetime.now()

        logger.debug(f"Portfolio refreshed: {len(self._positions)} positions, "
                    f"value: {snapshot.total_value:,.2f}")

        return snapshot

    async def _load_positions(self):
        """Load positions from database."""
        async with self._db.session() as session:
            stmt = select(Position).where(
                Position.account_id == self._account_id
            )
            result = await session.execute(stmt)
            positions = result.scalars().all()

            self._positions.clear()
            for pos in positions:
                if pos.quantity != 0:  # Only active positions
                    position_info = PositionInfo(
                        symbol=pos.symbol,
                        quantity=float(pos.quantity),
                        cost_price=float(pos.cost_price) if pos.cost_price else 0,
                        current_price=0,  # Will be updated
                        market_value=float(pos.market_value) if pos.market_value else 0,
                        unrealized_pnl=float(pos.unrealized_pnl) if pos.unrealized_pnl else 0,
                        realized_pnl=float(pos.realized_pnl) if pos.realized_pnl else 0,
                        pnl_percent=0,
                        position_type="LONG" if pos.quantity > 0 else "SHORT",
                        opened_at=pos.updated_at or datetime.now(),
                        updated_at=pos.updated_at or datetime.now(),
                        account_id=pos.account_id,
                        currency=pos.currency or "HKD"
                    )
                    self._positions[pos.symbol] = position_info

    async def _sync_with_broker(self):
        """Sync positions with broker account."""
        if not self._trade_context:
            return

        try:
            # Get account balance
            account_balances = await self._trade_context.account_balance()
            cash_total = 0.0
            if account_balances:
                for balance in account_balances:
                    cash_infos = getattr(balance, "cash_infos", []) or []
                    for cash_info in cash_infos:
                        available_cash = getattr(cash_info, "available_cash", None)
                        if available_cash is not None:
                            cash_total += float(available_cash)
                if cash_total == 0.0:
                    for balance in account_balances:
                        total_cash = getattr(balance, "total_cash", None)
                        if total_cash is not None:
                            cash_total += float(total_cash)

            if cash_total > 0.0:
                self._cash_balance = cash_total

            # Get positions
            stock_positions = await self._trade_context.stock_positions()
            channels = []
            if hasattr(stock_positions, "channels"):
                channels = getattr(stock_positions, "channels") or []
            elif isinstance(stock_positions, list):
                channels = [type("_TmpChannel", (), {"account_channel": self._account_id, "positions": stock_positions})()]

            seen_symbols: set[str] = set()

            async with self._db.session() as session:
                for channel in channels:
                    account_id = getattr(channel, "account_channel", None) or self._account_id
                    if self._account_id == "default":
                        self._account_id = account_id

                    for pos in getattr(channel, "positions", []) or []:
                        symbol = getattr(pos, "symbol", None)
                        if not symbol:
                            continue

                        quantity = float(getattr(pos, "quantity", 0) or 0)
                        available_quantity = float(getattr(pos, "available_quantity", 0) or 0)
                        cost_price = float(getattr(pos, "cost_price", 0) or 0)
                        currency = getattr(pos, "currency", None) or "HKD"

                        if quantity == 0:
                            self._positions.pop(symbol, None)
                            continue

                        seen_symbols.add(symbol)

                        # Update in-memory representation
                        position_info = self._positions.get(symbol)
                        if not position_info:
                            position_info = PositionInfo(
                                symbol=symbol,
                                quantity=quantity,
                                cost_price=cost_price,
                                current_price=0.0,
                                market_value=0.0,
                                unrealized_pnl=0.0,
                                realized_pnl=0.0,
                                pnl_percent=0.0,
                                position_type="LONG" if quantity >= 0 else "SHORT",
                                opened_at=datetime.now(),
                                updated_at=datetime.now(),
                                account_id=account_id,
                                currency=currency,
                            )
                            self._positions[symbol] = position_info
                        else:
                            position_info.quantity = quantity
                            position_info.cost_price = cost_price
                            position_info.position_type = "LONG" if quantity >= 0 else "SHORT"
                            position_info.updated_at = datetime.now()
                            position_info.currency = currency

                        # Persist to database
                        stmt = insert(Position).values(
                            account_id=account_id,
                            symbol=symbol,
                            quantity=Decimal(str(quantity)),
                            available_quantity=Decimal(str(available_quantity)),
                            currency=currency,
                            cost_price=Decimal(str(cost_price)) if cost_price else None,
                            market_value=Decimal("0"),
                            unrealized_pnl=Decimal("0"),
                            realized_pnl=Decimal("0"),
                            updated_at=datetime.now(),
                        )
                        stmt = stmt.on_conflict_do_update(
                            index_elements=[Position.account_id, Position.symbol],
                            set_={
                                "quantity": stmt.excluded.quantity,
                                "available_quantity": stmt.excluded.available_quantity,
                                "currency": stmt.excluded.currency,
                                "cost_price": stmt.excluded.cost_price,
                                "updated_at": stmt.excluded.updated_at,
                            },
                        )
                        await session.execute(stmt)

                # Remove positions no longer present
                if seen_symbols:
                    delete_stmt = (
                        delete(Position)
                        .where(Position.account_id == self._account_id)
                        .where(~Position.symbol.in_(seen_symbols))
                    )
                    await session.execute(delete_stmt)
                else:
                    await session.execute(
                        delete(Position).where(Position.account_id == self._account_id)
                    )

                await session.commit()

            # Clean up in-memory positions that vanished
            stale_symbols = [sym for sym in self._positions.keys() if sym not in seen_symbols]
            for sym in stale_symbols:
                self._positions.pop(sym, None)

            logger.debug(
                "Synced %d positions with broker (cash: %.2f)",
                len(self._positions),
                self._cash_balance,
            )

        except Exception as e:
            logger.error(f"Error syncing with broker: {e}")

    async def _update_market_prices(self):
        """Update current market prices for positions."""
        if not self._positions:
            return

        symbols = list(self._positions.keys())

        async with self._db.session() as session:
            # Get latest prices from database
            for symbol in symbols:
                stmt = select(KlineDaily).where(
                    KlineDaily.symbol == symbol
                ).order_by(KlineDaily.trade_date.desc()).limit(1)

                result = await session.execute(stmt)
                kline = result.scalar_one_or_none()

                if kline and symbol in self._positions:
                    current_price = float(kline.close)
                    position = self._positions[symbol]

                    # Update position metrics
                    position.current_price = current_price
                    position.market_value = position.quantity * current_price

                    # Calculate P&L
                    if position.cost_price > 0:
                        position.unrealized_pnl = (current_price - position.cost_price) * position.quantity
                        position.pnl_percent = ((current_price - position.cost_price) / position.cost_price) * 100

    async def _calculate_snapshot(self) -> PortfolioSnapshot:
        """Calculate current portfolio snapshot."""
        total_value = self._cash_balance
        long_exposure = 0.0
        short_exposure = 0.0

        for position in self._positions.values():
            total_value += position.market_value

            if position.quantity > 0:
                long_exposure += abs(position.market_value)
            else:
                short_exposure += abs(position.market_value)

        gross_exposure = long_exposure + short_exposure
        net_exposure = long_exposure - short_exposure

        return PortfolioSnapshot(
            timestamp=datetime.now(),
            account_id=self._account_id,
            cash=self._cash_balance,
            total_value=total_value,
            positions=self._positions.copy(),
            long_exposure=long_exposure,
            short_exposure=short_exposure,
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            position_count=len(self._positions),
            currency="HKD"
        )

    async def get_position(self, symbol: str) -> Optional[PositionInfo]:
        """
        Get position information for a symbol.

        Args:
            symbol: Symbol to query

        Returns:
            Position info or None
        """
        return self._positions.get(symbol)

    async def get_positions(self) -> List[PositionInfo]:
        """
        Get all open positions.

        Returns:
            List of position information
        """
        return list(self._positions.values())

    def position_size(self, symbol: str) -> float:
        """
        Get current position size for a symbol.

        Args:
            symbol: Symbol to query

        Returns:
            Position size (positive for long, negative for short)
        """
        position = self._positions.get(symbol)
        return position.quantity if position else 0.0

    async def get_cash_balance(self) -> float:
        """
        Get current cash balance.

        Returns:
            Available cash
        """
        return self._cash_balance

    async def update_position(
        self,
        symbol: str,
        quantity: float,
        price: float,
        is_buy: bool
    ) -> PositionInfo:
        """
        Update position after a trade.

        Args:
            symbol: Symbol traded
            quantity: Trade quantity
            price: Trade price
            is_buy: True for buy, False for sell

        Returns:
            Updated position info
        """
        trade_quantity = quantity if is_buy else -quantity

        if symbol in self._positions:
            position = self._positions[symbol]
            old_quantity = position.quantity
            new_quantity = old_quantity + trade_quantity

            if new_quantity == 0:
                # Position closed
                realized_pnl = (price - position.cost_price) * abs(old_quantity)
                position.realized_pnl += realized_pnl
                del self._positions[symbol]
                logger.info(f"Closed position {symbol}, realized P&L: {realized_pnl:,.2f}")

                # Update cash
                self._cash_balance += price * abs(old_quantity)

            elif (old_quantity > 0 and new_quantity > 0) or (old_quantity < 0 and new_quantity < 0):
                # Adding to position
                total_cost = position.cost_price * abs(old_quantity) + price * quantity
                position.quantity = new_quantity
                position.cost_price = total_cost / abs(new_quantity)
                position.updated_at = datetime.now()

                # Update cash
                self._cash_balance -= price * quantity if is_buy else -price * quantity

            else:
                # Reversing position
                # First close existing
                realized_pnl = (price - position.cost_price) * abs(old_quantity)
                position.realized_pnl += realized_pnl

                # Then open new position
                position.quantity = new_quantity
                position.cost_price = price
                position.position_type = "LONG" if new_quantity > 0 else "SHORT"
                position.updated_at = datetime.now()

                # Update cash
                self._cash_balance += price * abs(old_quantity)  # From closing
                self._cash_balance -= price * abs(new_quantity)  # From opening

        else:
            # New position
            position = PositionInfo(
                symbol=symbol,
                quantity=trade_quantity,
                cost_price=price,
                current_price=price,
                market_value=trade_quantity * price,
                unrealized_pnl=0,
                realized_pnl=0,
                pnl_percent=0,
                position_type="LONG" if trade_quantity > 0 else "SHORT",
                opened_at=datetime.now(),
                updated_at=datetime.now(),
                account_id=self._account_id
            )
            self._positions[symbol] = position

            # Update cash
            self._cash_balance -= price * quantity if is_buy else -price * quantity

            logger.info(f"Opened position {symbol}: {trade_quantity} @ {price:,.2f}")

        # Persist to database
        await self._persist_position(position)

        return position

    async def _persist_position(self, position: PositionInfo):
        """Persist position to database."""
        async with self._db.session() as session:
            stmt = insert(Position).values(
                account_id=self._account_id,
                symbol=position.symbol,
                quantity=Decimal(str(position.quantity)),
                available_quantity=Decimal(str(position.quantity)),
                currency=position.currency,
                cost_price=Decimal(str(position.cost_price)),
                market_value=Decimal(str(position.market_value)),
                unrealized_pnl=Decimal(str(position.unrealized_pnl)),
                realized_pnl=Decimal(str(position.realized_pnl)),
                updated_at=position.updated_at
            )

            stmt = stmt.on_conflict_do_update(
                index_elements=['account_id', 'symbol'],
                set_={
                    'quantity': stmt.excluded.quantity,
                    'available_quantity': stmt.excluded.available_quantity,
                    'cost_price': stmt.excluded.cost_price,
                    'market_value': stmt.excluded.market_value,
                    'unrealized_pnl': stmt.excluded.unrealized_pnl,
                    'realized_pnl': stmt.excluded.realized_pnl,
                    'updated_at': stmt.excluded.updated_at
                }
            )

            await session.execute(stmt)
            await session.commit()

    async def close_position(self, symbol: str, price: float) -> float:
        """
        Close a position at market price.

        Args:
            symbol: Symbol to close
            price: Closing price

        Returns:
            Realized P&L
        """
        position = self._positions.get(symbol)
        if not position:
            logger.warning(f"No position to close for {symbol}")
            return 0.0

        # Calculate realized P&L
        realized_pnl = (price - position.cost_price) * position.quantity

        # Update cash
        self._cash_balance += price * abs(position.quantity)

        # Remove position
        del self._positions[symbol]

        # Delete from database
        async with self._db.session() as session:
            stmt = delete(Position).where(
                and_(
                    Position.account_id == self._account_id,
                    Position.symbol == symbol
                )
            )
            await session.execute(stmt)
            await session.commit()

        logger.info(f"Closed position {symbol} @ {price:,.2f}, P&L: {realized_pnl:,.2f}")
        return realized_pnl

    async def calculate_metrics(
        self,
        lookback_days: int = 30
    ) -> PortfolioMetrics:
        """
        Calculate portfolio performance metrics.

        Args:
            lookback_days: Days to look back for metrics

        Returns:
            Portfolio metrics
        """
        # Get historical trades
        trades = await self._get_historical_trades(lookback_days)

        if not trades:
            return PortfolioMetrics(
                total_return=0, daily_return=0, sharpe_ratio=0,
                max_drawdown=0, win_rate=0, profit_factor=0,
                avg_win=0, avg_loss=0, total_trades=0,
                winning_trades=0, losing_trades=0
            )

        # Calculate trade statistics
        pnls = [t['pnl'] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_trades = len(trades)
        winning_trades = len(wins)
        losing_trades = len(losses)

        win_rate = winning_trades / total_trades if total_trades > 0 else 0
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0

        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # Calculate returns
        current_value = self._cache.total_value if self._cache else 0
        initial_value = 1000000  # Placeholder - should track actual initial value
        total_return = (current_value - initial_value) / initial_value if initial_value > 0 else 0

        # Daily returns (simplified)
        daily_returns = np.diff([t['value'] for t in trades]) / [t['value'] for t in trades][:-1]
        daily_return = np.mean(daily_returns) if len(daily_returns) > 0 else 0

        # Sharpe ratio (simplified - assuming risk-free rate of 0)
        sharpe_ratio = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252) if len(daily_returns) > 1 else 0

        # Max drawdown (simplified)
        values = [t['value'] for t in trades]
        if values:
            peak = np.maximum.accumulate(values)
            drawdown = (values - peak) / peak
            max_drawdown = np.min(drawdown)
        else:
            max_drawdown = 0

        return PortfolioMetrics(
            total_return=total_return,
            daily_return=daily_return,
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_win=avg_win,
            avg_loss=avg_loss,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades
        )

    async def _get_historical_trades(self, days: int) -> List[Dict[str, Any]]:
        """Get historical trade data."""
        cutoff_date = datetime.now() - timedelta(days=days)

        async with self._db.session() as session:
            stmt = select(OrderRecord).where(
                and_(
                    OrderRecord.created_at >= cutoff_date,
                    OrderRecord.status == "FILLED"
                )
            ).order_by(OrderRecord.created_at)

            result = await session.execute(stmt)
            orders = result.scalars().all()

            trades = []
            for order in orders:
                # Simplified - in production would track actual P&L
                trades.append({
                    'timestamp': order.created_at,
                    'symbol': order.symbol,
                    'quantity': float(order.quantity),
                    'price': float(order.price),
                    'pnl': 0,  # Would calculate actual P&L
                    'value': self._cash_balance  # Simplified
                })

            return trades

    async def reconcile_with_broker(self):
        """Reconcile local state with broker positions."""
        if not self._trade_context:
            logger.warning("No broker connection for reconciliation")
            return

        await self._sync_with_broker()
        await self._persist_all_positions()

        logger.info("Portfolio reconciliation completed")

    async def _persist_all_positions(self):
        """Persist all positions to database."""
        for position in self._positions.values():
            await self._persist_position(position)

    def get_snapshot(self) -> Optional[PortfolioSnapshot]:
        """Get cached portfolio snapshot."""
        return self._cache

    async def get_pnl_history(self, days: int = 30) -> pd.DataFrame:
        """
        Get P&L history.

        Args:
            days: Number of days to look back

        Returns:
            DataFrame with P&L history
        """
        trades = await self._get_historical_trades(days)

        if not trades:
            return pd.DataFrame()

        df = pd.DataFrame(trades)
        df['date'] = pd.to_datetime(df['timestamp']).dt.date

        # Group by date and calculate daily P&L
        daily_pnl = df.groupby('date')['pnl'].sum()
        cumulative_pnl = daily_pnl.cumsum()

        result = pd.DataFrame({
            'daily_pnl': daily_pnl,
            'cumulative_pnl': cumulative_pnl
        })

        return result
