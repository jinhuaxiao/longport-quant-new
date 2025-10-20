"""Volume Breakout Strategy."""

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


class VolumeBreakoutStrategy(StrategyBase):
    """
    Volume Breakout Strategy.

    Generates signals based on volume spikes combined with price breakouts:
    - Buy signal when price breaks above resistance with high volume
    - Sell signal when price breaks below support with high volume
    """

    def __init__(
        self,
        name: str = "Volume_Breakout",
        volume_multiplier: float = 2.0,
        price_breakout_pct: float = 0.02,
        lookback_period: int = 20,
        min_data_points: int = 50,
        position_size: float = 0.15,
        stop_loss: float = 0.04,
        take_profit: float = 0.12,
        use_atr_stops: bool = True,
    ):
        """
        Initialize Volume Breakout Strategy.

        Args:
            name: Strategy name
            volume_multiplier: Multiple of average volume to trigger signal
            price_breakout_pct: Percentage price move to confirm breakout
            lookback_period: Period for calculating resistance/support
            min_data_points: Minimum data points required
            position_size: Position size as fraction of capital
            stop_loss: Stop loss percentage
            take_profit: Take profit percentage
            use_atr_stops: Use ATR-based dynamic stops
        """
        super().__init__(name)
        self.volume_multiplier = volume_multiplier
        self.price_breakout_pct = price_breakout_pct
        self.lookback_period = lookback_period
        self.min_data_points = min_data_points
        self.position_size = position_size
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.use_atr_stops = use_atr_stops

    async def generate_signals(
        self,
        symbol: str,
        market_data: Optional[Dict[str, Any]] = None
    ) -> List[Signal]:
        """
        Generate trading signals based on volume breakout patterns.

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

            # Calculate volume indicators
            df['volume_sma'] = TechnicalIndicators.sma(df['volume'].values, 20)
            df['volume_ratio'] = df['volume'] / df['volume_sma']

            # Calculate price levels
            df['resistance'] = df['high'].rolling(window=self.lookback_period).max()
            df['support'] = df['low'].rolling(window=self.lookback_period).min()
            df['midpoint'] = (df['resistance'] + df['support']) / 2

            # Calculate ATR for dynamic stops
            if self.use_atr_stops:
                df['atr'] = TechnicalIndicators.atr(
                    df['high'].values,
                    df['low'].values,
                    df['close'].values,
                    period=14
                )

            # Calculate OBV for trend confirmation
            df['obv'] = TechnicalIndicators.obv(df['close'].values, df['volume'].values)
            df['obv_sma'] = TechnicalIndicators.sma(df['obv'].values, 10)

            # Get current and previous data
            current = df.iloc[-1]
            previous = df.iloc[-2] if len(df) > 1 else None
            lookback = df.iloc[-self.lookback_period:]

            if previous is not None and not np.isnan(current['volume_ratio']):
                # Check for volume spike
                volume_spike = current['volume_ratio'] >= self.volume_multiplier

                # Check for upside breakout with volume
                if (volume_spike and
                    current['close'] > previous['resistance'] and
                    current['close'] > previous['close'] * (1 + self.price_breakout_pct)):

                    # Calculate breakout strength
                    breakout_strength = self._calculate_breakout_strength(
                        current,
                        previous,
                        lookback,
                        direction='up'
                    )

                    # Dynamic stops based on ATR
                    if self.use_atr_stops and not np.isnan(current['atr']):
                        stop_loss_price = float(current['close'] - 2 * current['atr'])
                        take_profit_price = float(current['close'] + 3 * current['atr'])
                    else:
                        stop_loss_price = float(current['close'] * (1 - self.stop_loss))
                        take_profit_price = float(current['close'] * (1 + self.take_profit))

                    signal = Signal(
                        symbol=symbol,
                        strategy=self.name,
                        signal_type="BUY",
                        strength=breakout_strength,
                        price_target=float(current['close']),
                        stop_loss=stop_loss_price,
                        take_profit=take_profit_price,
                        metadata={
                            'volume_ratio': float(current['volume_ratio']),
                            'breakout_level': float(previous['resistance']),
                            'price_change_pct': float((current['close'] - previous['close']) / previous['close']),
                            'obv_trend': 'bullish' if current['obv'] > current['obv_sma'] else 'bearish',
                            'pattern': 'volume_breakout_up',
                            'atr': float(current['atr']) if not np.isnan(current['atr']) else None
                        }
                    )
                    signals.append(signal)
                    logger.info(f"Volume breakout UP detected for {symbol}: Volume ratio={current['volume_ratio']:.2f}")

                # Check for downside breakout with volume
                elif (volume_spike and
                      current['close'] < previous['support'] and
                      current['close'] < previous['close'] * (1 - self.price_breakout_pct)):

                    # Calculate breakout strength
                    breakout_strength = self._calculate_breakout_strength(
                        current,
                        previous,
                        lookback,
                        direction='down'
                    )

                    # Dynamic stops based on ATR
                    if self.use_atr_stops and not np.isnan(current['atr']):
                        stop_loss_price = float(current['close'] + 2 * current['atr'])
                        take_profit_price = float(current['close'] - 3 * current['atr'])
                    else:
                        stop_loss_price = float(current['close'] * (1 + self.stop_loss))
                        take_profit_price = float(current['close'] * (1 - self.take_profit))

                    signal = Signal(
                        symbol=symbol,
                        strategy=self.name,
                        signal_type="SELL",
                        strength=breakout_strength,
                        price_target=float(current['close']),
                        stop_loss=stop_loss_price,
                        take_profit=take_profit_price,
                        metadata={
                            'volume_ratio': float(current['volume_ratio']),
                            'breakout_level': float(previous['support']),
                            'price_change_pct': float((current['close'] - previous['close']) / previous['close']),
                            'obv_trend': 'bullish' if current['obv'] > current['obv_sma'] else 'bearish',
                            'pattern': 'volume_breakout_down',
                            'atr': float(current['atr']) if not np.isnan(current['atr']) else None
                        }
                    )
                    signals.append(signal)
                    logger.info(f"Volume breakout DOWN detected for {symbol}: Volume ratio={current['volume_ratio']:.2f}")

                # Check for accumulation/distribution patterns
                elif self._check_accumulation_pattern(lookback):
                    signal = Signal(
                        symbol=symbol,
                        strategy=self.name,
                        signal_type="ACCUMULATION",
                        strength=60.0,
                        price_target=float(current['close']),
                        metadata={
                            'volume_trend': 'accumulation',
                            'obv_slope': self._calculate_obv_slope(lookback),
                            'pattern': 'smart_money_accumulation'
                        }
                    )
                    signals.append(signal)
                    logger.info(f"Accumulation pattern detected for {symbol}")

                elif self._check_distribution_pattern(lookback):
                    signal = Signal(
                        symbol=symbol,
                        strategy=self.name,
                        signal_type="DISTRIBUTION",
                        strength=60.0,
                        price_target=float(current['close']),
                        metadata={
                            'volume_trend': 'distribution',
                            'obv_slope': self._calculate_obv_slope(lookback),
                            'pattern': 'smart_money_distribution'
                        }
                    )
                    signals.append(signal)
                    logger.info(f"Distribution pattern detected for {symbol}")

        except Exception as e:
            logger.error(f"Error generating volume breakout signals for {symbol}: {e}")

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

    def _calculate_breakout_strength(
        self,
        current: pd.Series,
        previous: pd.Series,
        lookback: pd.DataFrame,
        direction: str
    ) -> float:
        """
        Calculate breakout signal strength.

        Args:
            current: Current bar data
            previous: Previous bar data
            lookback: Lookback period data
            direction: 'up' or 'down'

        Returns:
            Signal strength (0-100)
        """
        strength = 0.0

        # Volume component (up to 40 points)
        volume_score = min(40, (current['volume_ratio'] - 1) * 20)
        strength += volume_score

        # Price movement component (up to 30 points)
        price_change = abs(current['close'] - previous['close']) / previous['close']
        price_score = min(30, price_change * 500)
        strength += price_score

        # Trend alignment component (up to 20 points)
        if direction == 'up':
            if current['obv'] > current['obv_sma']:
                strength += 20
        else:
            if current['obv'] < current['obv_sma']:
                strength += 20

        # Range expansion component (up to 10 points)
        current_range = current['high'] - current['low']
        avg_range = (lookback['high'] - lookback['low']).mean()
        if current_range > avg_range * 1.5:
            strength += 10

        return min(100, max(0, strength))

    def _check_accumulation_pattern(self, df: pd.DataFrame) -> bool:
        """
        Check for accumulation pattern (rising OBV with stable price).

        Args:
            df: DataFrame with price and volume data

        Returns:
            True if accumulation pattern detected
        """
        if len(df) < 10:
            return False

        try:
            # Check if OBV is rising while price is relatively stable
            obv_slope = self._calculate_obv_slope(df)
            price_volatility = df['close'].pct_change().std()

            # Accumulation: Rising OBV with low price volatility
            if obv_slope > 0.02 and price_volatility < 0.02:
                # Additional confirmation: Higher lows in OBV
                obv_lows = []
                for i in range(1, len(df) - 1):
                    if df.iloc[i]['obv'] < df.iloc[i-1]['obv'] and df.iloc[i]['obv'] < df.iloc[i+1]['obv']:
                        obv_lows.append(df.iloc[i]['obv'])

                if len(obv_lows) >= 2 and obv_lows[-1] > obv_lows[-2]:
                    return True

            return False

        except Exception as e:
            logger.debug(f"Error checking accumulation pattern: {e}")
            return False

    def _check_distribution_pattern(self, df: pd.DataFrame) -> bool:
        """
        Check for distribution pattern (falling OBV with stable price).

        Args:
            df: DataFrame with price and volume data

        Returns:
            True if distribution pattern detected
        """
        if len(df) < 10:
            return False

        try:
            # Check if OBV is falling while price is relatively stable
            obv_slope = self._calculate_obv_slope(df)
            price_volatility = df['close'].pct_change().std()

            # Distribution: Falling OBV with low price volatility
            if obv_slope < -0.02 and price_volatility < 0.02:
                # Additional confirmation: Lower highs in OBV
                obv_highs = []
                for i in range(1, len(df) - 1):
                    if df.iloc[i]['obv'] > df.iloc[i-1]['obv'] and df.iloc[i]['obv'] > df.iloc[i+1]['obv']:
                        obv_highs.append(df.iloc[i]['obv'])

                if len(obv_highs) >= 2 and obv_highs[-1] < obv_highs[-2]:
                    return True

            return False

        except Exception as e:
            logger.debug(f"Error checking distribution pattern: {e}")
            return False

    def _calculate_obv_slope(self, df: pd.DataFrame) -> float:
        """
        Calculate OBV trend slope.

        Args:
            df: DataFrame with OBV data

        Returns:
            Normalized slope of OBV
        """
        if len(df) < 5 or 'obv' not in df.columns:
            return 0.0

        try:
            obv_values = df['obv'].values[-10:]
            x = np.arange(len(obv_values))
            coefficients = np.polyfit(x, obv_values, 1)
            slope = coefficients[0]

            # Normalize by average OBV
            avg_obv = np.mean(obv_values)
            if avg_obv != 0:
                normalized_slope = slope / avg_obv
                return float(normalized_slope)

            return 0.0

        except Exception as e:
            logger.debug(f"Error calculating OBV slope: {e}")
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
            # Skip weak signals
            if signal.strength < 50:
                logger.debug(f"Signal strength too low: {signal.strength}")
                return False

            # Check for existing positions
            if hasattr(self, 'portfolio_service'):
                positions = await self.portfolio_service.get_positions()

                # Avoid duplicate positions for buy signals
                if signal.symbol in [p.symbol for p in positions]:
                    if signal.signal_type in ["BUY"]:
                        logger.debug(f"Already have position in {signal.symbol}")
                        return False

            # Additional validation for accumulation/distribution signals
            if signal.signal_type in ["ACCUMULATION", "DISTRIBUTION"]:
                # These are informational signals, always valid if generated
                return True

            return True

        except Exception as e:
            logger.error(f"Error validating signal: {e}")
            return False

    def get_parameters(self) -> Dict[str, Any]:
        """Get strategy parameters."""
        return {
            'volume_multiplier': self.volume_multiplier,
            'price_breakout_pct': self.price_breakout_pct,
            'lookback_period': self.lookback_period,
            'min_data_points': self.min_data_points,
            'position_size': self.position_size,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'use_atr_stops': self.use_atr_stops
        }

    def set_parameters(self, parameters: Dict[str, Any]):
        """Update strategy parameters."""
        if 'volume_multiplier' in parameters:
            self.volume_multiplier = parameters['volume_multiplier']
        if 'price_breakout_pct' in parameters:
            self.price_breakout_pct = parameters['price_breakout_pct']
        if 'lookback_period' in parameters:
            self.lookback_period = parameters['lookback_period']
        if 'min_data_points' in parameters:
            self.min_data_points = parameters['min_data_points']
        if 'position_size' in parameters:
            self.position_size = parameters['position_size']
        if 'stop_loss' in parameters:
            self.stop_loss = parameters['stop_loss']
        if 'take_profit' in parameters:
            self.take_profit = parameters['take_profit']
        if 'use_atr_stops' in parameters:
            self.use_atr_stops = parameters['use_atr_stops']