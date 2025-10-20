"""Bollinger Bands Strategy."""

from __future__ import annotations

from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

from loguru import logger
from longport_quant.strategy.base import StrategyBase
from longport_quant.common.types import Signal
from longport_quant.features.technical_indicators import TechnicalIndicators
from longport_quant.persistence.models import KlineDaily
from sqlalchemy import select, and_


class BollingerBandsStrategy(StrategyBase):
    """
    Bollinger Bands Strategy.

    Generates signals based on Bollinger Bands:
    - Buy when price touches lower band and rebounds (mean reversion)
    - Sell when price touches upper band and reverses
    - Breakout signals when price breaks bands with momentum
    """

    def __init__(
        self,
        name: str = "Bollinger_Bands",
        period: int = 20,
        std_dev: float = 2.0,
        min_data_points: int = 50,
        position_size: float = 0.1,
        stop_loss: float = 0.03,
        take_profit: float = 0.08,
        use_squeeze: bool = True,
        use_momentum: bool = True,
    ):
        """
        Initialize Bollinger Bands Strategy.

        Args:
            name: Strategy name
            period: Period for moving average and standard deviation
            std_dev: Number of standard deviations for bands
            min_data_points: Minimum data points required
            position_size: Position size as fraction of capital
            stop_loss: Stop loss percentage
            take_profit: Take profit percentage
            use_squeeze: Whether to detect Bollinger Band squeeze
            use_momentum: Whether to use momentum confirmation
        """
        super().__init__(name)
        self.period = period
        self.std_dev = std_dev
        self.min_data_points = max(min_data_points, period + 10)
        self.position_size = position_size
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.use_squeeze = use_squeeze
        self.use_momentum = use_momentum

    async def generate_signals(
        self,
        symbol: str,
        market_data: Optional[Dict[str, Any]] = None
    ) -> List[Signal]:
        """
        Generate trading signals based on Bollinger Bands patterns.

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

            # Calculate Bollinger Bands
            bb_result = TechnicalIndicators.bollinger_bands(
                df['close'].values,
                period=self.period,
                std_dev=self.std_dev
            )
            df['bb_upper'] = bb_result['upper']
            df['bb_middle'] = bb_result['middle']
            df['bb_lower'] = bb_result['lower']
            df['bb_width'] = bb_result['width']
            df['bb_percent'] = bb_result['percent']

            # Calculate additional indicators
            if self.use_momentum:
                df['rsi'] = TechnicalIndicators.rsi(df['close'].values, 14)
                df['momentum'] = df['close'].pct_change(10)

            # Volume analysis
            df['volume_sma'] = TechnicalIndicators.sma(df['volume'].values, 20)
            df['volume_ratio'] = df['volume'] / df['volume_sma']

            # Get current and previous data
            current = df.iloc[-1]
            previous = df.iloc[-2] if len(df) > 1 else None
            lookback = df.iloc[-20:] if len(df) >= 20 else df

            if previous is not None and not np.isnan(current['bb_upper']):
                # Check for squeeze breakout
                if self.use_squeeze:
                    squeeze_signal = self._check_squeeze_breakout(lookback, current)
                    if squeeze_signal:
                        signals.append(squeeze_signal)

                # Mean Reversion Signals
                # Buy signal: Price touches/crosses below lower band
                if (current['close'] <= current['bb_lower'] and
                    previous['close'] > previous['bb_lower']):

                    # Check for oversold bounce confirmation
                    bounce_confirmed = False
                    if self.use_momentum and not np.isnan(current.get('rsi', 0)):
                        bounce_confirmed = current['rsi'] < 35

                    signal_strength = self._calculate_mean_reversion_strength(
                        current,
                        lookback,
                        direction='up'
                    )

                    signal = Signal(
                        symbol=symbol,
                        strategy=self.name,
                        signal_type="BUY",
                        strength=signal_strength,
                        price_target=float(current['close']),
                        stop_loss=float(current['bb_lower'] * 0.98),  # Just below lower band
                        take_profit=float(current['bb_middle']),  # Target middle band
                        metadata={
                            'bb_upper': float(current['bb_upper']),
                            'bb_middle': float(current['bb_middle']),
                            'bb_lower': float(current['bb_lower']),
                            'bb_width': float(current['bb_width']),
                            'bb_percent': float(current['bb_percent']),
                            'pattern': 'lower_band_bounce',
                            'rsi': float(current.get('rsi', 0)) if 'rsi' in current else None,
                            'bounce_confirmed': bounce_confirmed
                        }
                    )
                    signals.append(signal)
                    logger.info(f"Lower band bounce detected for {symbol}: Price={current['close']:.2f}, Lower Band={current['bb_lower']:.2f}")

                # Sell signal: Price touches/crosses above upper band
                elif (current['close'] >= current['bb_upper'] and
                      previous['close'] < previous['bb_upper']):

                    # Check for overbought reversal confirmation
                    reversal_confirmed = False
                    if self.use_momentum and not np.isnan(current.get('rsi', 0)):
                        reversal_confirmed = current['rsi'] > 65

                    signal_strength = self._calculate_mean_reversion_strength(
                        current,
                        lookback,
                        direction='down'
                    )

                    signal = Signal(
                        symbol=symbol,
                        strategy=self.name,
                        signal_type="SELL",
                        strength=signal_strength,
                        price_target=float(current['close']),
                        stop_loss=float(current['bb_upper'] * 1.02),  # Just above upper band
                        take_profit=float(current['bb_middle']),  # Target middle band
                        metadata={
                            'bb_upper': float(current['bb_upper']),
                            'bb_middle': float(current['bb_middle']),
                            'bb_lower': float(current['bb_lower']),
                            'bb_width': float(current['bb_width']),
                            'bb_percent': float(current['bb_percent']),
                            'pattern': 'upper_band_reversal',
                            'rsi': float(current.get('rsi', 0)) if 'rsi' in current else None,
                            'reversal_confirmed': reversal_confirmed
                        }
                    )
                    signals.append(signal)
                    logger.info(f"Upper band reversal detected for {symbol}: Price={current['close']:.2f}, Upper Band={current['bb_upper']:.2f}")

                # Trend Following Signals
                # Strong uptrend: Price walking the upper band
                elif self._check_walking_bands(lookback, direction='up'):
                    signal = Signal(
                        symbol=symbol,
                        strategy=self.name,
                        signal_type="TREND_UP",
                        strength=70.0,
                        price_target=float(current['close']),
                        stop_loss=float(current['bb_middle']),
                        take_profit=float(current['close'] * 1.05),
                        metadata={
                            'bb_upper': float(current['bb_upper']),
                            'bb_middle': float(current['bb_middle']),
                            'bb_width': float(current['bb_width']),
                            'pattern': 'walking_upper_band',
                            'trend_strength': self._calculate_trend_strength(lookback)
                        }
                    )
                    signals.append(signal)
                    logger.info(f"Walking upper band detected for {symbol}")

                # Strong downtrend: Price walking the lower band
                elif self._check_walking_bands(lookback, direction='down'):
                    signal = Signal(
                        symbol=symbol,
                        strategy=self.name,
                        signal_type="TREND_DOWN",
                        strength=70.0,
                        price_target=float(current['close']),
                        stop_loss=float(current['bb_middle']),
                        take_profit=float(current['close'] * 0.95),
                        metadata={
                            'bb_lower': float(current['bb_lower']),
                            'bb_middle': float(current['bb_middle']),
                            'bb_width': float(current['bb_width']),
                            'pattern': 'walking_lower_band',
                            'trend_strength': self._calculate_trend_strength(lookback)
                        }
                    )
                    signals.append(signal)
                    logger.info(f"Walking lower band detected for {symbol}")

                # Breakout signals with expanded bands
                if current['bb_width'] > lookback['bb_width'].mean() * 1.5:
                    if current['close'] > current['bb_upper'] and current['volume_ratio'] > 1.5:
                        signal = Signal(
                            symbol=symbol,
                            strategy=self.name,
                            signal_type="BREAKOUT_UP",
                            strength=80.0,
                            price_target=float(current['close']),
                            stop_loss=float(current['bb_upper']),
                            take_profit=float(current['close'] * (1 + self.take_profit * 1.5)),
                            metadata={
                                'bb_width': float(current['bb_width']),
                                'volume_ratio': float(current['volume_ratio']),
                                'pattern': 'volatility_breakout_up'
                            }
                        )
                        signals.append(signal)
                        logger.info(f"Volatility breakout UP detected for {symbol}")

                    elif current['close'] < current['bb_lower'] and current['volume_ratio'] > 1.5:
                        signal = Signal(
                            symbol=symbol,
                            strategy=self.name,
                            signal_type="BREAKOUT_DOWN",
                            strength=80.0,
                            price_target=float(current['close']),
                            stop_loss=float(current['bb_lower']),
                            take_profit=float(current['close'] * (1 - self.take_profit * 1.5)),
                            metadata={
                                'bb_width': float(current['bb_width']),
                                'volume_ratio': float(current['volume_ratio']),
                                'pattern': 'volatility_breakout_down'
                            }
                        )
                        signals.append(signal)
                        logger.info(f"Volatility breakout DOWN detected for {symbol}")

        except Exception as e:
            logger.error(f"Error generating Bollinger Bands signals for {symbol}: {e}")

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

    def _check_squeeze_breakout(self, lookback: pd.DataFrame, current: pd.Series) -> Optional[Signal]:
        """
        Check for Bollinger Band squeeze and potential breakout.

        Args:
            lookback: Historical data
            current: Current bar data

        Returns:
            Signal if squeeze breakout detected, None otherwise
        """
        if len(lookback) < 10:
            return None

        try:
            # Calculate squeeze indicator (band width relative to average)
            avg_width = lookback['bb_width'].mean()
            min_width = lookback['bb_width'].min()

            # Squeeze detected: Current width near minimum
            if current['bb_width'] < avg_width * 0.5:
                # Check for directional bias
                price_position = (current['close'] - current['bb_lower']) / (current['bb_upper'] - current['bb_lower'])

                if price_position > 0.7:  # Price near upper band during squeeze
                    return Signal(
                        symbol=current.name if hasattr(current, 'name') else 'UNKNOWN',
                        strategy=self.name,
                        signal_type="SQUEEZE_BULLISH",
                        strength=75.0,
                        price_target=float(current['close']),
                        metadata={
                            'bb_width': float(current['bb_width']),
                            'avg_width': float(avg_width),
                            'squeeze_ratio': float(current['bb_width'] / avg_width),
                            'pattern': 'bollinger_squeeze_bullish'
                        }
                    )
                elif price_position < 0.3:  # Price near lower band during squeeze
                    return Signal(
                        symbol=current.name if hasattr(current, 'name') else 'UNKNOWN',
                        strategy=self.name,
                        signal_type="SQUEEZE_BEARISH",
                        strength=75.0,
                        price_target=float(current['close']),
                        metadata={
                            'bb_width': float(current['bb_width']),
                            'avg_width': float(avg_width),
                            'squeeze_ratio': float(current['bb_width'] / avg_width),
                            'pattern': 'bollinger_squeeze_bearish'
                        }
                    )

            # Check for squeeze release (expansion after contraction)
            elif min_width < avg_width * 0.5 and current['bb_width'] > avg_width:
                # Squeeze released - strong directional move likely
                if current['close'] > current['bb_middle']:
                    return Signal(
                        symbol=current.name if hasattr(current, 'name') else 'UNKNOWN',
                        strategy=self.name,
                        signal_type="SQUEEZE_RELEASE_UP",
                        strength=85.0,
                        price_target=float(current['close']),
                        stop_loss=float(current['bb_middle']),
                        take_profit=float(current['close'] * 1.1),
                        metadata={
                            'bb_width': float(current['bb_width']),
                            'expansion_ratio': float(current['bb_width'] / min_width),
                            'pattern': 'squeeze_release_bullish'
                        }
                    )
                else:
                    return Signal(
                        symbol=current.name if hasattr(current, 'name') else 'UNKNOWN',
                        strategy=self.name,
                        signal_type="SQUEEZE_RELEASE_DOWN",
                        strength=85.0,
                        price_target=float(current['close']),
                        stop_loss=float(current['bb_middle']),
                        take_profit=float(current['close'] * 0.9),
                        metadata={
                            'bb_width': float(current['bb_width']),
                            'expansion_ratio': float(current['bb_width'] / min_width),
                            'pattern': 'squeeze_release_bearish'
                        }
                    )

            return None

        except Exception as e:
            logger.debug(f"Error checking squeeze breakout: {e}")
            return None

    def _check_walking_bands(self, df: pd.DataFrame, direction: str) -> bool:
        """
        Check if price is walking the bands (strong trend).

        Args:
            df: DataFrame with Bollinger Bands data
            direction: 'up' or 'down'

        Returns:
            True if walking bands pattern detected
        """
        if len(df) < 5:
            return False

        try:
            recent = df.iloc[-5:]

            if direction == 'up':
                # Check if price consistently near upper band
                touches = 0
                for _, row in recent.iterrows():
                    if row['close'] >= row['bb_upper'] * 0.95:
                        touches += 1

                # At least 3 out of 5 bars near upper band
                return touches >= 3

            else:  # direction == 'down'
                # Check if price consistently near lower band
                touches = 0
                for _, row in recent.iterrows():
                    if row['close'] <= row['bb_lower'] * 1.05:
                        touches += 1

                # At least 3 out of 5 bars near lower band
                return touches >= 3

        except Exception as e:
            logger.debug(f"Error checking walking bands: {e}")
            return False

    def _calculate_mean_reversion_strength(
        self,
        current: pd.Series,
        lookback: pd.DataFrame,
        direction: str
    ) -> float:
        """
        Calculate mean reversion signal strength.

        Args:
            current: Current bar data
            lookback: Historical data
            direction: 'up' or 'down'

        Returns:
            Signal strength (0-100)
        """
        strength = 0.0

        try:
            # Band position component (up to 40 points)
            if direction == 'up':
                # How far below lower band
                distance = (current['bb_lower'] - current['close']) / current['bb_lower']
                strength += min(40, abs(distance) * 200)
            else:
                # How far above upper band
                distance = (current['close'] - current['bb_upper']) / current['bb_upper']
                strength += min(40, abs(distance) * 200)

            # Band width component (up to 20 points)
            # Wider bands = stronger reversal potential
            width_percentile = (current['bb_width'] / lookback['bb_width'].max()) * 100
            strength += min(20, width_percentile * 0.2)

            # RSI confirmation (up to 20 points)
            if 'rsi' in current and not np.isnan(current['rsi']):
                if direction == 'up' and current['rsi'] < 30:
                    strength += 20
                elif direction == 'down' and current['rsi'] > 70:
                    strength += 20

            # Volume component (up to 20 points)
            if current['volume_ratio'] > 1.2:
                strength += min(20, (current['volume_ratio'] - 1) * 40)

            return min(100, max(0, strength))

        except Exception as e:
            logger.debug(f"Error calculating mean reversion strength: {e}")
            return 50.0

    def _calculate_trend_strength(self, df: pd.DataFrame) -> float:
        """
        Calculate trend strength using band position.

        Args:
            df: DataFrame with Bollinger Bands data

        Returns:
            Trend strength (-100 to 100, negative for downtrend)
        """
        if len(df) < 5:
            return 0.0

        try:
            # Calculate average band position
            positions = []
            for _, row in df.iloc[-5:].iterrows():
                if row['bb_upper'] != row['bb_lower']:
                    position = (row['close'] - row['bb_lower']) / (row['bb_upper'] - row['bb_lower'])
                    positions.append(position)

            if not positions:
                return 0.0

            avg_position = np.mean(positions)
            # Convert to -100 to 100 scale
            trend_strength = (avg_position - 0.5) * 200

            return float(trend_strength)

        except Exception as e:
            logger.debug(f"Error calculating trend strength: {e}")
            return 0.0

    async def validate_signal(self, signal: Signal) -> bool:
        """
        Validate signal against current market conditions.

        Args:
            signal: Signal to validate

        Returns:
            True if signal is valid
        """
        try:
            # Skip weak signals except for squeeze patterns
            if "SQUEEZE" not in signal.signal_type and signal.strength < 45:
                logger.debug(f"Signal strength too low: {signal.strength}")
                return False

            # Check for existing positions
            if hasattr(self, 'portfolio_service'):
                positions = await self.portfolio_service.get_positions()

                # Avoid duplicate positions for buy signals
                if signal.symbol in [p.symbol for p in positions]:
                    if signal.signal_type in ["BUY", "BREAKOUT_UP", "TREND_UP"]:
                        logger.debug(f"Already have position in {signal.symbol}")
                        return False

            # Validate squeeze signals separately
            if "SQUEEZE" in signal.signal_type:
                return signal.strength >= 70

            return True

        except Exception as e:
            logger.error(f"Error validating signal: {e}")
            return False

    def get_parameters(self) -> Dict[str, Any]:
        """Get strategy parameters."""
        return {
            'period': self.period,
            'std_dev': self.std_dev,
            'min_data_points': self.min_data_points,
            'position_size': self.position_size,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'use_squeeze': self.use_squeeze,
            'use_momentum': self.use_momentum
        }

    def set_parameters(self, parameters: Dict[str, Any]):
        """Update strategy parameters."""
        if 'period' in parameters:
            self.period = parameters['period']
        if 'std_dev' in parameters:
            self.std_dev = parameters['std_dev']
        if 'min_data_points' in parameters:
            self.min_data_points = max(parameters['min_data_points'], self.period + 10)
        if 'position_size' in parameters:
            self.position_size = parameters['position_size']
        if 'stop_loss' in parameters:
            self.stop_loss = parameters['stop_loss']
        if 'take_profit' in parameters:
            self.take_profit = parameters['take_profit']
        if 'use_squeeze' in parameters:
            self.use_squeeze = parameters['use_squeeze']
        if 'use_momentum' in parameters:
            self.use_momentum = parameters['use_momentum']