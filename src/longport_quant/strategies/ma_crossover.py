"""Moving Average Crossover Strategy."""

from __future__ import annotations

from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

from loguru import logger
from longport_quant.strategy.base import StrategyBase
from longport_quant.common.types import Signal
from longport_quant.features.technical_indicators import TechnicalIndicators
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import KlineDaily, Position
from sqlalchemy import select, and_


class MovingAverageCrossoverStrategy(StrategyBase):
    """
    Moving Average Crossover Strategy.

    Generates buy signals when fast MA crosses above slow MA (golden cross)
    and sell signals when fast MA crosses below slow MA (death cross).
    """

    def __init__(
        self,
        name: str = "MA_Crossover",
        fast_period: int = 5,
        slow_period: int = 20,
        min_data_points: int = 50,
        position_size: float = 0.1,  # 10% of capital per position
        stop_loss: float = 0.05,      # 5% stop loss
        take_profit: float = 0.15,    # 15% take profit
    ):
        """
        Initialize MA Crossover Strategy.

        Args:
            name: Strategy name
            fast_period: Period for fast moving average
            slow_period: Period for slow moving average
            min_data_points: Minimum data points required
            position_size: Position size as fraction of capital
            stop_loss: Stop loss percentage
            take_profit: Take profit percentage
        """
        super().__init__(name)
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.min_data_points = min_data_points
        self.position_size = position_size
        self.stop_loss = stop_loss
        self.take_profit = take_profit

        # Validate parameters
        if fast_period >= slow_period:
            raise ValueError("Fast period must be less than slow period")

    async def generate_signals(
        self,
        symbol: str,
        market_data: Optional[Dict[str, Any]] = None
    ) -> List[Signal]:
        """
        Generate trading signals based on MA crossover.

        Args:
            symbol: Symbol to generate signals for
            market_data: Optional market data (if not provided, fetches from DB)

        Returns:
            List of signals
        """
        signals = []

        try:
            # Get historical data
            if market_data:
                df = pd.DataFrame(market_data)
            else:
                df = await self._fetch_historical_data(symbol)

            if df is None or len(df) < self.min_data_points:
                logger.debug(f"Insufficient data for {symbol}: {len(df) if df is not None else 0} points")
                return signals

            # Calculate moving averages
            df['ma_fast'] = TechnicalIndicators.sma(df['close'].values, self.fast_period)
            df['ma_slow'] = TechnicalIndicators.sma(df['close'].values, self.slow_period)

            # Get the last few rows for signal detection
            current = df.iloc[-1]
            previous = df.iloc[-2] if len(df) > 1 else None

            if previous is not None and not np.isnan(current['ma_fast']) and not np.isnan(current['ma_slow']):
                # Check for golden cross (buy signal)
                if (previous['ma_fast'] <= previous['ma_slow'] and
                    current['ma_fast'] > current['ma_slow']):

                    signal_strength = self._calculate_signal_strength(
                        current['ma_fast'],
                        current['ma_slow'],
                        current['close']
                    )

                    signal = Signal(
                        symbol=symbol,
                        strategy=self.name,
                        signal_type="BUY",
                        strength=signal_strength,
                        price_target=float(current['close']),
                        stop_loss=float(current['close'] * (1 - self.stop_loss)),
                        take_profit=float(current['close'] * (1 + self.take_profit)),
                        metadata={
                            'ma_fast': float(current['ma_fast']),
                            'ma_slow': float(current['ma_slow']),
                            'crossover_type': 'golden_cross',
                            'fast_period': self.fast_period,
                            'slow_period': self.slow_period
                        }
                    )
                    signals.append(signal)
                    logger.info(f"Golden cross detected for {symbol}: {signal}")

                # Check for death cross (sell signal)
                elif (previous['ma_fast'] >= previous['ma_slow'] and
                      current['ma_fast'] < current['ma_slow']):

                    signal_strength = self._calculate_signal_strength(
                        current['ma_slow'],
                        current['ma_fast'],
                        current['close']
                    )

                    signal = Signal(
                        symbol=symbol,
                        strategy=self.name,
                        signal_type="SELL",
                        strength=signal_strength,
                        price_target=float(current['close']),
                        stop_loss=float(current['close'] * (1 + self.stop_loss)),  # Inverse for short
                        take_profit=float(current['close'] * (1 - self.take_profit)),
                        metadata={
                            'ma_fast': float(current['ma_fast']),
                            'ma_slow': float(current['ma_slow']),
                            'crossover_type': 'death_cross',
                            'fast_period': self.fast_period,
                            'slow_period': self.slow_period
                        }
                    )
                    signals.append(signal)
                    logger.info(f"Death cross detected for {symbol}: {signal}")

                # Check for trend confirmation (optional signal)
                else:
                    trend_strength = self._analyze_trend(df)
                    if trend_strength > 0.7:  # Strong uptrend
                        if current['ma_fast'] > current['ma_slow']:
                            signal = Signal(
                                symbol=symbol,
                                strategy=self.name,
                                signal_type="HOLD_LONG",
                                strength=trend_strength,
                                price_target=float(current['close']),
                                metadata={
                                    'ma_fast': float(current['ma_fast']),
                                    'ma_slow': float(current['ma_slow']),
                                    'trend': 'uptrend',
                                    'trend_strength': trend_strength
                                }
                            )
                            signals.append(signal)

        except Exception as e:
            logger.error(f"Error generating signals for {symbol}: {e}")

        return signals

    async def _fetch_historical_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        Fetch historical data from database.

        Args:
            symbol: Symbol to fetch data for

        Returns:
            DataFrame with OHLCV data or None
        """
        try:
            # Use the database session from strategy manager
            if not hasattr(self, 'db'):
                logger.error("Database connection not available")
                return None

            async with self.db.session() as session:
                # Fetch recent daily K-lines
                end_date = datetime.now().date()
                start_date = end_date - timedelta(days=self.min_data_points * 2)

                stmt = select(KlineDaily).where(
                    and_(
                        KlineDaily.symbol == symbol,
                        KlineDaily.trade_date >= start_date,
                        KlineDaily.trade_date <= end_date
                    )
                ).order_by(KlineDaily.trade_date)

                result = await session.execute(stmt)
                klines = result.scalars().all()

                if not klines:
                    return None

                # Convert to DataFrame
                df = pd.DataFrame([
                    {
                        'timestamp': k.trade_date,
                        'open': float(k.open),
                        'high': float(k.high),
                        'low': float(k.low),
                        'close': float(k.close),
                        'volume': k.volume
                    }
                    for k in klines
                ])

                return df

        except Exception as e:
            logger.error(f"Error fetching historical data for {symbol}: {e}")
            return None

    def _calculate_signal_strength(
        self,
        ma_above: float,
        ma_below: float,
        current_price: float
    ) -> float:
        """
        Calculate signal strength based on MA separation and price position.

        Args:
            ma_above: MA value that crossed above
            ma_below: MA value that crossed below
            current_price: Current price

        Returns:
            Signal strength (0-100)
        """
        # Base strength from MA separation
        separation = abs(ma_above - ma_below) / ma_below
        strength = min(separation * 100, 50)  # Max 50 from separation

        # Adjust based on price position
        price_above_both = current_price > max(ma_above, ma_below)
        price_below_both = current_price < min(ma_above, ma_below)

        if price_above_both:
            strength += 25  # Bullish confirmation
        elif price_below_both:
            strength -= 10  # Bearish warning

        # Add momentum component
        if hasattr(self, '_last_signals'):
            # Check if we're in a trend
            recent_signals = [s for s in self._last_signals if s.signal_type == "BUY"]
            if len(recent_signals) > 2:
                strength += 15  # Trend continuation

        return max(0, min(100, strength))

    def _analyze_trend(self, df: pd.DataFrame) -> float:
        """
        Analyze overall trend strength.

        Args:
            df: DataFrame with price data

        Returns:
            Trend strength (-1 to 1, negative for downtrend)
        """
        if len(df) < 20:
            return 0.0

        # Calculate trend using linear regression
        prices = df['close'].values[-20:]
        x = np.arange(len(prices))
        coefficients = np.polyfit(x, prices, 1)
        slope = coefficients[0]

        # Normalize slope
        avg_price = np.mean(prices)
        normalized_slope = slope / avg_price * 100

        # Calculate R-squared for trend strength
        predicted = np.polyval(coefficients, x)
        ss_res = np.sum((prices - predicted) ** 2)
        ss_tot = np.sum((prices - np.mean(prices)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0

        # Combine slope and R-squared
        trend_strength = np.tanh(normalized_slope) * r_squared

        return float(trend_strength)

    async def validate_signal(self, signal: Signal) -> bool:
        """
        Validate signal against current market conditions and risk rules.

        Args:
            signal: Signal to validate

        Returns:
            True if signal is valid
        """
        try:
            # Check if we already have a position in this symbol
            if hasattr(self, 'portfolio_service'):
                positions = await self.portfolio_service.get_positions()
                if signal.symbol in [p.symbol for p in positions]:
                    if signal.signal_type == "BUY":
                        logger.debug(f"Already have position in {signal.symbol}, skipping buy signal")
                        return False

            # Additional validation logic
            if signal.strength < 30:
                logger.debug(f"Signal strength too low: {signal.strength}")
                return False

            # Check volatility (if we have recent data)
            # This would need actual implementation based on your needs

            return True

        except Exception as e:
            logger.error(f"Error validating signal: {e}")
            return False

    def get_parameters(self) -> Dict[str, Any]:
        """Get strategy parameters."""
        return {
            'fast_period': self.fast_period,
            'slow_period': self.slow_period,
            'min_data_points': self.min_data_points,
            'position_size': self.position_size,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit
        }

    def set_parameters(self, parameters: Dict[str, Any]):
        """Update strategy parameters."""
        if 'fast_period' in parameters:
            self.fast_period = parameters['fast_period']
        if 'slow_period' in parameters:
            self.slow_period = parameters['slow_period']
        if 'min_data_points' in parameters:
            self.min_data_points = parameters['min_data_points']
        if 'position_size' in parameters:
            self.position_size = parameters['position_size']
        if 'stop_loss' in parameters:
            self.stop_loss = parameters['stop_loss']
        if 'take_profit' in parameters:
            self.take_profit = parameters['take_profit']

        # Validate after update
        if self.fast_period >= self.slow_period:
            raise ValueError("Fast period must be less than slow period")