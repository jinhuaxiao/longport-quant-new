"""Backtesting engine for strategies."""

from __future__ import annotations

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
from decimal import Decimal
import pandas as pd
import numpy as np
from collections import defaultdict

from loguru import logger
from longport_quant.strategy.base import Strategy, Signal
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import KlineDaily, KlineMinute
from sqlalchemy import select, and_


@dataclass
class BacktestConfig:
    """Backtesting configuration."""

    start_date: date
    end_date: date
    initial_capital: float = 100000.0
    commission_rate: float = 0.001  # 0.1% commission
    slippage_rate: float = 0.0005  # 0.05% slippage
    max_position_size: float = 0.2  # Max 20% per position
    max_positions: int = 5  # Max number of concurrent positions
    use_minute_data: bool = False  # Use minute data for more accurate fills
    benchmark_symbol: Optional[str] = None  # Benchmark for comparison


@dataclass
class Position:
    """Represents a position in backtesting."""

    symbol: str
    quantity: int
    entry_price: float
    entry_date: datetime
    exit_price: Optional[float] = None
    exit_date: Optional[datetime] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    pnl: float = 0.0
    pnl_percent: float = 0.0
    is_open: bool = True
    entry_signal: Optional[Signal] = None


@dataclass
class BacktestResult:
    """Backtesting results."""

    # Performance metrics
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0

    # Trade statistics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    avg_holding_period: timedelta = timedelta(0)

    # Portfolio metrics
    final_capital: float = 0.0
    peak_capital: float = 0.0
    total_commission: float = 0.0
    total_slippage: float = 0.0

    # Time series data
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: List[Position] = field(default_factory=list)
    daily_returns: pd.Series = field(default_factory=pd.Series)

    # Benchmark comparison
    benchmark_return: Optional[float] = None
    alpha: Optional[float] = None
    beta: Optional[float] = None

    # Additional statistics
    metrics: Dict[str, Any] = field(default_factory=dict)


class BacktestEngine:
    """Engine for backtesting trading strategies."""

    def __init__(self, db: DatabaseSessionManager):
        """
        Initialize backtest engine.

        Args:
            db: Database session manager
        """
        self.db = db
        self._price_cache: Dict[str, pd.DataFrame] = {}

    async def run_backtest(
        self,
        strategy: Strategy,
        symbols: List[str],
        config: BacktestConfig
    ) -> BacktestResult:
        """
        Run backtest for a strategy.

        Args:
            strategy: Strategy to backtest
            symbols: List of symbols to trade
            config: Backtesting configuration

        Returns:
            BacktestResult with performance metrics
        """
        logger.info(f"Starting backtest for {strategy.name} from {config.start_date} to {config.end_date}")

        # Initialize portfolio state
        capital = config.initial_capital
        positions: Dict[str, Position] = {}
        closed_trades: List[Position] = []
        equity_history = []
        daily_returns = []

        # Load historical data
        await self._load_historical_data(symbols, config)

        # Get trading days
        trading_days = await self._get_trading_days(config.start_date, config.end_date)

        # Simulate each trading day
        prev_capital = capital
        for current_date in trading_days:
            # Update positions with current prices
            capital = await self._update_positions(
                positions, closed_trades, current_date, capital, config
            )

            # Generate signals for each symbol
            for symbol in symbols:
                if len(positions) >= config.max_positions:
                    break

                # Get market data up to current date
                market_data = self._get_market_data_until(symbol, current_date)
                if market_data is None or len(market_data) < 20:
                    continue

                # Generate signals
                signals = await strategy.generate_signals(symbol, market_data)

                # Process signals
                for signal in signals:
                    capital = await self._process_signal(
                        signal, positions, closed_trades,
                        current_date, capital, config
                    )

            # Record daily equity
            total_value = capital + sum(
                p.quantity * self._get_current_price(p.symbol, current_date)
                for p in positions.values()
            )
            equity_history.append({
                'date': current_date,
                'capital': capital,
                'total_value': total_value,
                'positions': len(positions)
            })

            # Calculate daily return
            if prev_capital > 0:
                daily_return = (total_value - prev_capital) / prev_capital
                daily_returns.append(daily_return)
                prev_capital = total_value

        # Close all remaining positions
        for position in list(positions.values()):
            self._close_position(
                position,
                self._get_current_price(position.symbol, config.end_date),
                config.end_date,
                config
            )
            closed_trades.append(position)
            capital += position.quantity * position.exit_price * (1 - config.commission_rate - config.slippage_rate)

        # Calculate metrics
        result = self._calculate_metrics(
            closed_trades,
            equity_history,
            daily_returns,
            config
        )

        # Load benchmark data if specified
        if config.benchmark_symbol:
            result.benchmark_return = await self._calculate_benchmark_return(
                config.benchmark_symbol, config.start_date, config.end_date
            )
            if result.benchmark_return is not None:
                result.alpha = result.total_return - result.benchmark_return

        logger.info(f"Backtest completed. Total return: {result.total_return:.2%}, "
                   f"Sharpe ratio: {result.sharpe_ratio:.2f}, "
                   f"Max drawdown: {result.max_drawdown:.2%}")

        return result

    async def _load_historical_data(
        self,
        symbols: List[str],
        config: BacktestConfig
    ) -> None:
        """Load historical data for all symbols."""
        unique_symbols = list(dict.fromkeys(symbols))
        if not unique_symbols:
            return

        async with self.db.session() as session:
            if config.use_minute_data:
                stmt = (
                    select(
                        KlineMinute.symbol,
                        KlineMinute.timestamp.label("ts"),
                        KlineMinute.open,
                        KlineMinute.high,
                        KlineMinute.low,
                        KlineMinute.close,
                        KlineMinute.volume,
                    )
                    .where(
                        KlineMinute.symbol.in_(unique_symbols),
                        KlineMinute.timestamp >= datetime.combine(
                            config.start_date, datetime.min.time()
                        ),
                        KlineMinute.timestamp <= datetime.combine(
                            config.end_date, datetime.max.time()
                        ),
                    )
                    .order_by(KlineMinute.symbol, KlineMinute.timestamp)
                )
            else:
                stmt = (
                    select(
                        KlineDaily.symbol,
                        KlineDaily.trade_date.label("ts"),
                        KlineDaily.open,
                        KlineDaily.high,
                        KlineDaily.low,
                        KlineDaily.close,
                        KlineDaily.volume,
                    )
                    .where(
                        KlineDaily.symbol.in_(unique_symbols),
                        KlineDaily.trade_date >= config.start_date,
                        KlineDaily.trade_date <= config.end_date,
                    )
                    .order_by(KlineDaily.symbol, KlineDaily.trade_date)
                )

            result = await session.execute(stmt)
            records = result.mappings().all()

        if not records:
            return

        data_by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        for row in records:
            timestamp = row["ts"]
            symbol = row["symbol"]
            if timestamp is None or symbol is None:
                continue

            data_by_symbol[symbol].append(
                {
                    "timestamp": timestamp,
                    "open": float(row["open"]) if row["open"] is not None else None,
                    "high": float(row["high"]) if row["high"] is not None else None,
                    "low": float(row["low"]) if row["low"] is not None else None,
                    "close": float(row["close"]) if row["close"] is not None else None,
                    "volume": int(row["volume"]) if row["volume"] is not None else None,
                }
            )

        for symbol in unique_symbols:
            rows = data_by_symbol.get(symbol)
            if not rows:
                continue

            df = pd.DataFrame(rows)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
            self._price_cache[symbol] = df
            logger.debug(f"Loaded {len(df)} bars for {symbol}")

    async def _get_trading_days(
        self,
        start_date: date,
        end_date: date
    ) -> List[date]:
        """Get list of trading days."""
        # For simplicity, use all weekdays
        # In production, should query TradingCalendar table
        trading_days = []
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:  # Monday to Friday
                trading_days.append(current)
            current += timedelta(days=1)
        return trading_days

    def _get_market_data_until(
        self,
        symbol: str,
        current_date: date
    ) -> Optional[Dict[str, Any]]:
        """Get market data up to current date."""
        if symbol not in self._price_cache:
            return None

        df = self._price_cache[symbol]
        # Filter data up to current date
        if isinstance(df.index[0], pd.Timestamp):
            mask = df.index.date <= current_date
        else:
            mask = df.index <= current_date

        historical = df[mask]

        if historical.empty:
            return None

        return {
            'timestamp': historical.index.tolist(),
            'open': historical['open'].tolist(),
            'high': historical['high'].tolist(),
            'low': historical['low'].tolist(),
            'close': historical['close'].tolist(),
            'volume': historical['volume'].tolist()
        }

    def _get_current_price(self, symbol: str, current_date: date) -> float:
        """Get current price for a symbol."""
        if symbol not in self._price_cache:
            return 0.0

        df = self._price_cache[symbol]

        # Find closest price
        if isinstance(df.index[0], pd.Timestamp):
            mask = df.index.date <= current_date
        else:
            mask = df.index <= current_date

        historical = df[mask]

        if historical.empty:
            return 0.0

        return float(historical.iloc[-1]['close'])

    async def _update_positions(
        self,
        positions: Dict[str, Position],
        closed_trades: List[Position],
        current_date: date,
        capital: float,
        config: BacktestConfig
    ) -> float:
        """Update positions and check stop loss/take profit."""
        for symbol, position in list(positions.items()):
            current_price = self._get_current_price(symbol, current_date)

            if current_price == 0:
                continue

            # Check stop loss
            if position.stop_loss and current_price <= position.stop_loss:
                self._close_position(position, position.stop_loss, current_date, config)
                closed_trades.append(position)
                capital += position.quantity * position.exit_price * (1 - config.commission_rate - config.slippage_rate)
                del positions[symbol]
                logger.debug(f"Stop loss triggered for {symbol} at {position.exit_price:.2f}")

            # Check take profit
            elif position.take_profit and current_price >= position.take_profit:
                self._close_position(position, position.take_profit, current_date, config)
                closed_trades.append(position)
                capital += position.quantity * position.exit_price * (1 - config.commission_rate - config.slippage_rate)
                del positions[symbol]
                logger.debug(f"Take profit triggered for {symbol} at {position.exit_price:.2f}")

        return capital

    async def _process_signal(
        self,
        signal: Signal,
        positions: Dict[str, Position],
        closed_trades: List[Position],
        current_date: date,
        capital: float,
        config: BacktestConfig
    ) -> float:
        """Process a trading signal."""
        # Check if we already have a position
        if signal.symbol in positions:
            position = positions[signal.symbol]

            # Handle sell signals
            if signal.signal_type in ["SELL", "STRONG_SELL"]:
                current_price = self._get_current_price(signal.symbol, current_date)
                self._close_position(position, current_price, current_date, config)
                closed_trades.append(position)
                capital += position.quantity * position.exit_price * (1 - config.commission_rate - config.slippage_rate)
                del positions[signal.symbol]
                logger.debug(f"Closed position in {signal.symbol} at {current_price:.2f}")

        else:
            # Handle buy signals
            if signal.signal_type in ["BUY", "STRONG_BUY"]:
                if len(positions) < config.max_positions:
                    # Calculate position size
                    position_value = min(
                        capital * config.max_position_size,
                        capital / (config.max_positions - len(positions))
                    )

                    current_price = self._get_current_price(signal.symbol, current_date)
                    if current_price > 0:
                        # Apply slippage to entry
                        entry_price = current_price * (1 + config.slippage_rate)
                        quantity = int(position_value / entry_price)

                        if quantity > 0:
                            # Create position
                            position = Position(
                                symbol=signal.symbol,
                                quantity=quantity,
                                entry_price=entry_price,
                                entry_date=datetime.combine(current_date, datetime.min.time()),
                                stop_loss=signal.stop_loss,
                                take_profit=signal.take_profit,
                                entry_signal=signal
                            )

                            positions[signal.symbol] = position
                            capital -= quantity * entry_price * (1 + config.commission_rate)
                            logger.debug(f"Opened position in {signal.symbol}: "
                                       f"{quantity} shares at {entry_price:.2f}")

        return capital

    def _close_position(
        self,
        position: Position,
        exit_price: float,
        exit_date: date,
        config: BacktestConfig
    ) -> None:
        """Close a position and calculate P&L."""
        # Apply slippage to exit
        position.exit_price = exit_price * (1 - config.slippage_rate)
        position.exit_date = datetime.combine(exit_date, datetime.min.time())
        position.is_open = False

        # Calculate P&L
        gross_pnl = (position.exit_price - position.entry_price) * position.quantity
        commission = (position.entry_price + position.exit_price) * position.quantity * config.commission_rate
        position.pnl = gross_pnl - commission
        position.pnl_percent = position.pnl / (position.entry_price * position.quantity)

    def _calculate_metrics(
        self,
        trades: List[Position],
        equity_history: List[Dict],
        daily_returns: List[float],
        config: BacktestConfig
    ) -> BacktestResult:
        """Calculate performance metrics."""
        result = BacktestResult()

        if not trades:
            result.final_capital = config.initial_capital
            return result

        # Trade statistics
        result.total_trades = len(trades)
        winning_trades = [t for t in trades if t.pnl > 0]
        losing_trades = [t for t in trades if t.pnl < 0]

        result.winning_trades = len(winning_trades)
        result.losing_trades = len(losing_trades)
        result.win_rate = result.winning_trades / result.total_trades if result.total_trades > 0 else 0

        # P&L statistics
        if winning_trades:
            result.avg_win = np.mean([t.pnl for t in winning_trades])
            result.largest_win = max(t.pnl for t in winning_trades)

        if losing_trades:
            result.avg_loss = np.mean([t.pnl for t in losing_trades])
            result.largest_loss = min(t.pnl for t in losing_trades)

        # Profit factor
        gross_profit = sum(t.pnl for t in winning_trades) if winning_trades else 0
        gross_loss = abs(sum(t.pnl for t in losing_trades)) if losing_trades else 1
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # Holding period
        holding_periods = [
            t.exit_date - t.entry_date for t in trades
            if t.exit_date and t.entry_date
        ]
        if holding_periods:
            result.avg_holding_period = sum(holding_periods, timedelta()) / len(holding_periods)

        # Portfolio metrics
        if equity_history:
            equity_df = pd.DataFrame(equity_history)
            result.equity_curve = equity_df

            result.final_capital = equity_df.iloc[-1]['total_value']
            result.peak_capital = equity_df['total_value'].max()
            result.total_return = (result.final_capital - config.initial_capital) / config.initial_capital

            # Annual return
            days = (config.end_date - config.start_date).days
            if days > 0:
                years = days / 365.25
                result.annual_return = (1 + result.total_return) ** (1/years) - 1

            # Maximum drawdown
            rolling_max = equity_df['total_value'].expanding().max()
            drawdown = (equity_df['total_value'] - rolling_max) / rolling_max
            result.max_drawdown = drawdown.min()

        # Sharpe ratio
        if daily_returns:
            returns_array = np.array(daily_returns)
            if len(returns_array) > 1:
                result.sharpe_ratio = np.sqrt(252) * returns_array.mean() / returns_array.std()
                result.daily_returns = pd.Series(daily_returns)

        # Store trades
        result.trades = trades

        # Commission and slippage
        result.total_commission = sum(
            t.quantity * (t.entry_price + (t.exit_price or t.entry_price)) * config.commission_rate
            for t in trades
        )
        result.total_slippage = sum(
            t.quantity * t.entry_price * config.slippage_rate +
            t.quantity * (t.exit_price or t.entry_price) * config.slippage_rate
            for t in trades
        )

        return result

    async def _calculate_benchmark_return(
        self,
        symbol: str,
        start_date: date,
        end_date: date
    ) -> Optional[float]:
        """Calculate benchmark return."""
        if symbol not in self._price_cache:
            # Load benchmark data if not cached
            await self._load_historical_data([symbol], BacktestConfig(start_date, end_date))

        if symbol not in self._price_cache:
            return None

        df = self._price_cache[symbol]

        # Get start and end prices
        start_mask = df.index.date >= start_date
        end_mask = df.index.date <= end_date

        filtered = df[start_mask & end_mask]

        if len(filtered) < 2:
            return None

        start_price = filtered.iloc[0]['close']
        end_price = filtered.iloc[-1]['close']

        return (end_price - start_price) / start_price
