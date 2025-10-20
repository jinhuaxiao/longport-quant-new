"""RSI Reversal Strategy."""

from __future__ import annotations

from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

from loguru import logger
from longport_quant.strategy.base import StrategyBase
from longport_quant.common.types import Signal
from longport_quant.features.technical_indicators import TechnicalIndicators
from longport_quant.persistence.models import KlineDaily, Position
from sqlalchemy import select, and_


class RSIReversalStrategy(StrategyBase):
    """
    RSI Reversal Strategy.

    Generates signals based on RSI oversold/overbought conditions:
    - Buy signal when RSI crosses above oversold level (30) from below
    - Sell signal when RSI crosses below overbought level (70) from above
    """

    def __init__(
        self,
        name: str = "RSI_Reversal",
        rsi_period: int = 14,
        oversold_level: float = 30.0,
        overbought_level: float = 70.0,
        min_data_points: int = 30,
        position_size: float = 0.1,
        stop_loss: float = 0.03,
        take_profit: float = 0.08,
        use_divergence: bool = True,
    ):
        """
        Initialize RSI Reversal Strategy.

        Args:
            name: Strategy name
            rsi_period: Period for RSI calculation
            oversold_level: RSI level considered oversold
            overbought_level: RSI level considered overbought
            min_data_points: Minimum data points required
            position_size: Position size as fraction of capital
            stop_loss: Stop loss percentage
            take_profit: Take profit percentage
            use_divergence: Whether to check for price/RSI divergence
        """
        super().__init__(name)
        self.rsi_period = rsi_period
        self.oversold_level = oversold_level
        self.overbought_level = overbought_level
        self.min_data_points = max(min_data_points, rsi_period + 10)
        self.position_size = position_size
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.use_divergence = use_divergence

    async def generate_signals(
        self,
        symbol: str,
        market_data: Optional[Dict[str, Any]] = None
    ) -> List[Signal]:
        """
        Generate trading signals based on RSI reversal patterns.

        Args:
            symbol: Symbol to generate signals for
            market_data: Optional market data

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

            # Calculate RSI
            df['rsi'] = TechnicalIndicators.rsi(df['close'].values, self.rsi_period)

            # Calculate additional features for signal confirmation
            df['sma_20'] = TechnicalIndicators.sma(df['close'].values, 20)
            df['volume_sma'] = TechnicalIndicators.sma(df['volume'].values, 20)

            # Get recent data for signal detection
            current = df.iloc[-1]
            previous = df.iloc[-2] if len(df) > 1 else None
            lookback = df.iloc[-10:] if len(df) >= 10 else df

            if previous is not None and not np.isnan(current['rsi']):
                # Check for oversold bounce (buy signal)
                if (previous['rsi'] <= self.oversold_level and
                    current['rsi'] > self.oversold_level):

                    # Check for divergence if enabled
                    divergence_score = 0.0
                    if self.use_divergence:
                        divergence_score = self._check_bullish_divergence(lookback)

                    # Calculate signal strength
                    signal_strength = self._calculate_buy_strength(
                        current['rsi'],
                        divergence_score,
                        current['volume'] / current['volume_sma'] if current['volume_sma'] > 0 else 1.0
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
                            'rsi': float(current['rsi']),
                            'rsi_period': self.rsi_period,
                            'oversold_level': self.oversold_level,
                            'divergence_score': divergence_score,
                            'volume_ratio': float(current['volume'] / current['volume_sma']) if current['volume_sma'] > 0 else 1.0,
                            'pattern': 'oversold_bounce'
                        }
                    )
                    signals.append(signal)
                    logger.info(f"RSI oversold bounce detected for {symbol}: RSI={current['rsi']:.2f}")

                # Check for overbought reversal (sell signal)
                elif (previous['rsi'] >= self.overbought_level and
                      current['rsi'] < self.overbought_level):

                    # Check for divergence if enabled
                    divergence_score = 0.0
                    if self.use_divergence:
                        divergence_score = self._check_bearish_divergence(lookback)

                    # Calculate signal strength
                    signal_strength = self._calculate_sell_strength(
                        current['rsi'],
                        divergence_score,
                        current['volume'] / current['volume_sma'] if current['volume_sma'] > 0 else 1.0
                    )

                    signal = Signal(
                        symbol=symbol,
                        strategy=self.name,
                        signal_type="SELL",
                        strength=signal_strength,
                        price_target=float(current['close']),
                        stop_loss=float(current['close'] * (1 + self.stop_loss)),
                        take_profit=float(current['close'] * (1 - self.take_profit)),
                        metadata={
                            'rsi': float(current['rsi']),
                            'rsi_period': self.rsi_period,
                            'overbought_level': self.overbought_level,
                            'divergence_score': divergence_score,
                            'volume_ratio': float(current['volume'] / current['volume_sma']) if current['volume_sma'] > 0 else 1.0,
                            'pattern': 'overbought_reversal'
                        }
                    )
                    signals.append(signal)
                    logger.info(f"RSI overbought reversal detected for {symbol}: RSI={current['rsi']:.2f}")

                # Check for extreme conditions (strong signals)
                elif current['rsi'] < 20:  # Extreme oversold
                    signal = Signal(
                        symbol=symbol,
                        strategy=self.name,
                        signal_type="STRONG_BUY",
                        strength=90.0,
                        price_target=float(current['close']),
                        stop_loss=float(current['close'] * (1 - self.stop_loss)),
                        take_profit=float(current['close'] * (1 + self.take_profit * 1.5)),
                        metadata={
                            'rsi': float(current['rsi']),
                            'pattern': 'extreme_oversold'
                        }
                    )
                    signals.append(signal)
                    logger.info(f"Extreme oversold condition for {symbol}: RSI={current['rsi']:.2f}")

                elif current['rsi'] > 80:  # Extreme overbought
                    signal = Signal(
                        symbol=symbol,
                        strategy=self.name,
                        signal_type="STRONG_SELL",
                        strength=90.0,
                        price_target=float(current['close']),
                        stop_loss=float(current['close'] * (1 + self.stop_loss)),
                        take_profit=float(current['close'] * (1 - self.take_profit * 1.5)),
                        metadata={
                            'rsi': float(current['rsi']),
                            'pattern': 'extreme_overbought'
                        }
                    )
                    signals.append(signal)
                    logger.info(f"Extreme overbought condition for {symbol}: RSI={current['rsi']:.2f}")

        except Exception as e:
            logger.error(f"Error generating RSI signals for {symbol}: {e}")

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

    def _check_bullish_divergence(self, df: pd.DataFrame) -> float:
        """
        Check for bullish divergence between price and RSI.

        Args:
            df: DataFrame with price and RSI data

        Returns:
            Divergence score (0-100)
        """
        if len(df) < 5:
            return 0.0

        try:
            # Find local minima in price and RSI
            price_lows = []
            rsi_lows = []

            for i in range(1, len(df) - 1):
                if df.iloc[i]['close'] < df.iloc[i-1]['close'] and df.iloc[i]['close'] < df.iloc[i+1]['close']:
                    price_lows.append((i, df.iloc[i]['close']))
                if df.iloc[i]['rsi'] < df.iloc[i-1]['rsi'] and df.iloc[i]['rsi'] < df.iloc[i+1]['rsi']:
                    rsi_lows.append((i, df.iloc[i]['rsi']))

            if len(price_lows) >= 2 and len(rsi_lows) >= 2:
                # Check if price makes lower low but RSI makes higher low
                if (price_lows[-1][1] < price_lows[-2][1] and
                    rsi_lows[-1][1] > rsi_lows[-2][1]):
                    # Calculate divergence strength
                    price_diff = abs(price_lows[-2][1] - price_lows[-1][1]) / price_lows[-2][1]
                    rsi_diff = abs(rsi_lows[-1][1] - rsi_lows[-2][1]) / max(rsi_lows[-2][1], 1)
                    return min(100, (price_diff + rsi_diff) * 100)

            return 0.0

        except Exception as e:
            logger.debug(f"Error checking bullish divergence: {e}")
            return 0.0

    def _check_bearish_divergence(self, df: pd.DataFrame) -> float:
        """
        Check for bearish divergence between price and RSI.

        Args:
            df: DataFrame with price and RSI data

        Returns:
            Divergence score (0-100)
        """
        if len(df) < 5:
            return 0.0

        try:
            # Find local maxima in price and RSI
            price_highs = []
            rsi_highs = []

            for i in range(1, len(df) - 1):
                if df.iloc[i]['close'] > df.iloc[i-1]['close'] and df.iloc[i]['close'] > df.iloc[i+1]['close']:
                    price_highs.append((i, df.iloc[i]['close']))
                if df.iloc[i]['rsi'] > df.iloc[i-1]['rsi'] and df.iloc[i]['rsi'] > df.iloc[i+1]['rsi']:
                    rsi_highs.append((i, df.iloc[i]['rsi']))

            if len(price_highs) >= 2 and len(rsi_highs) >= 2:
                # Check if price makes higher high but RSI makes lower high
                if (price_highs[-1][1] > price_highs[-2][1] and
                    rsi_highs[-1][1] < rsi_highs[-2][1]):
                    # Calculate divergence strength
                    price_diff = abs(price_highs[-1][1] - price_highs[-2][1]) / price_highs[-2][1]
                    rsi_diff = abs(rsi_highs[-2][1] - rsi_highs[-1][1]) / rsi_highs[-2][1]
                    return min(100, (price_diff + rsi_diff) * 100)

            return 0.0

        except Exception as e:
            logger.debug(f"Error checking bearish divergence: {e}")
            return 0.0

    def _calculate_buy_strength(
        self,
        current_rsi: float,
        divergence_score: float,
        volume_ratio: float
    ) -> float:
        """
        Calculate buy signal strength.

        Args:
            current_rsi: Current RSI value
            divergence_score: Divergence score (0-100)
            volume_ratio: Volume ratio vs average

        Returns:
            Signal strength (0-100)
        """
        # Base strength from RSI level
        rsi_strength = max(0, (self.oversold_level - current_rsi) * 2)

        # Add divergence component
        divergence_strength = divergence_score * 0.3

        # Volume confirmation
        volume_strength = 0
        if volume_ratio > 1.2:
            volume_strength = min(20, (volume_ratio - 1) * 40)

        # Combine components
        total_strength = rsi_strength + divergence_strength + volume_strength

        return min(100, max(0, total_strength))

    def _calculate_sell_strength(
        self,
        current_rsi: float,
        divergence_score: float,
        volume_ratio: float
    ) -> float:
        """
        Calculate sell signal strength.

        Args:
            current_rsi: Current RSI value
            divergence_score: Divergence score (0-100)
            volume_ratio: Volume ratio vs average

        Returns:
            Signal strength (0-100)
        """
        # Base strength from RSI level
        rsi_strength = max(0, (current_rsi - self.overbought_level) * 2)

        # Add divergence component
        divergence_strength = divergence_score * 0.3

        # Volume confirmation
        volume_strength = 0
        if volume_ratio > 1.2:
            volume_strength = min(20, (volume_ratio - 1) * 40)

        # Combine components
        total_strength = rsi_strength + divergence_strength + volume_strength

        return min(100, max(0, total_strength))

    async def validate_signal(self, signal: Signal) -> bool:
        """
        Validate signal against current market conditions.

        Args:
            signal: Signal to validate

        Returns:
            True if signal is valid
        """
        try:
            # Skip weak signals
            if signal.strength < 40:
                logger.debug(f"Signal strength too low: {signal.strength}")
                return False

            # Check for existing positions
            if hasattr(self, 'portfolio_service'):
                positions = await self.portfolio_service.get_positions()

                # Avoid duplicate positions
                if signal.symbol in [p.symbol for p in positions]:
                    if signal.signal_type in ["BUY", "STRONG_BUY"]:
                        logger.debug(f"Already have position in {signal.symbol}")
                        return False

            # Validate extreme signals more strictly
            if "STRONG" in signal.signal_type and signal.strength < 80:
                return False

            return True

        except Exception as e:
            logger.error(f"Error validating signal: {e}")
            return False

    def get_parameters(self) -> Dict[str, Any]:
        """Get strategy parameters."""
        return {
            'rsi_period': self.rsi_period,
            'oversold_level': self.oversold_level,
            'overbought_level': self.overbought_level,
            'min_data_points': self.min_data_points,
            'position_size': self.position_size,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'use_divergence': self.use_divergence
        }

    def set_parameters(self, parameters: Dict[str, Any]):
        """Update strategy parameters."""
        if 'rsi_period' in parameters:
            self.rsi_period = parameters['rsi_period']
        if 'oversold_level' in parameters:
            self.oversold_level = parameters['oversold_level']
        if 'overbought_level' in parameters:
            self.overbought_level = parameters['overbought_level']
        if 'min_data_points' in parameters:
            self.min_data_points = max(parameters['min_data_points'], self.rsi_period + 10)
        if 'position_size' in parameters:
            self.position_size = parameters['position_size']
        if 'stop_loss' in parameters:
            self.stop_loss = parameters['stop_loss']
        if 'take_profit' in parameters:
            self.take_profit = parameters['take_profit']
        if 'use_divergence' in parameters:
            self.use_divergence = parameters['use_divergence']