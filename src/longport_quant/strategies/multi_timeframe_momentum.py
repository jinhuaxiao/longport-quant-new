"""Multi-timeframe momentum strategy using enhanced base."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
from loguru import logger

from longport_quant.common.types import Signal
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.strategy.enhanced_base import (
    EnhancedStrategyBase,
    StrategyParameters,
    TimeFrame,
)


class MultiTimeframeMomentumStrategy(EnhancedStrategyBase):
    """
    Multi-timeframe momentum strategy.

    This strategy analyzes momentum across multiple timeframes to identify
    strong trending opportunities with high probability of continuation.
    """

    def __init__(
        self,
        db: DatabaseSessionManager,
        parameters: Optional[StrategyParameters] = None,
        **kwargs
    ):
        """Initialize multi-timeframe momentum strategy."""
        # Set default parameters
        if parameters is None:
            parameters = StrategyParameters(
                name="MultiTimeframeMomentum",
                version="1.0.0",
                timeframes=[TimeFrame.D1, TimeFrame.H4, TimeFrame.H1],
                lookback_periods={
                    TimeFrame.D1: 50,
                    TimeFrame.H4: 100,
                    TimeFrame.H1: 200,
                },
                custom_params={
                    "momentum_period": 20,
                    "rsi_period": 14,
                    "volume_multiplier": 1.5,
                    "min_trend_strength": 0.6,
                    "min_alignment_score": 0.7,
                }
            )

        super().__init__(db, parameters, **kwargs)
        self._momentum_cache: Dict[str, Dict[TimeFrame, float]] = {}

    async def on_quote(self, quote: dict) -> None:
        """Process quote and check for momentum signals."""
        symbol = quote.get("symbol")
        if not symbol or symbol not in self.parameters.symbols:
            return

        # Analyze for potential signal
        signal = await self.analyze(symbol)
        if signal:
            await self.dispatch(signal)

    async def analyze(self, symbol: str) -> Optional[Signal]:
        """Analyze symbol for multi-timeframe momentum."""
        try:
            # Get data for all timeframes
            mtf_data = await self.get_multi_timeframe_data(
                symbol, self.parameters.timeframes, 200
            )

            if not mtf_data:
                logger.debug(f"No data available for {symbol}")
                return None

            # Calculate momentum for each timeframe
            momentum_scores = {}
            for tf, data in mtf_data.items():
                if len(data) >= self.parameters.get_param("momentum_period", 20):
                    momentum = self._calculate_momentum(data, tf)
                    momentum_scores[tf] = momentum
                    self._momentum_cache.setdefault(symbol, {})[tf] = momentum

            # Check alignment across timeframes
            alignment = self._check_timeframe_alignment(momentum_scores)
            if alignment < self.parameters.get_param("min_alignment_score", 0.7):
                logger.debug(
                    f"Poor timeframe alignment for {symbol}: {alignment:.2f}"
                )
                return None

            # Determine signal direction
            avg_momentum = sum(momentum_scores.values()) / len(momentum_scores)
            if abs(avg_momentum) < 0.2:  # Too weak
                return None

            signal_type = "BUY" if avg_momentum > 0 else "SELL"

            # Get latest price data
            primary_tf = self.parameters.timeframes[0]
            price_data = mtf_data[primary_tf]
            current_price = price_data["close"].iloc[-1]

            # Calculate additional indicators
            features = await self._calculate_features(symbol, mtf_data)

            # Calculate position size based on volatility
            position_size = self._calculate_position_size(price_data, current_price)

            # Generate signal with strength scoring
            signal = await self.generate_signal(
                symbol=symbol,
                signal_type=signal_type,
                quantity=position_size,
                base_score=70 + alignment * 30,  # 70-100 based on alignment
                reason=f"Multi-timeframe momentum: {avg_momentum:.2f}",
                features=features
            )

            return signal

        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            return None

    def _calculate_momentum(self, data: pd.DataFrame, timeframe: TimeFrame) -> float:
        """Calculate momentum indicator."""
        period = self.parameters.get_param("momentum_period", 20)

        if len(data) < period:
            return 0.0

        # Rate of change
        roc = (data["close"].iloc[-1] / data["close"].iloc[-period] - 1) * 100

        # RSI
        rsi_period = self.parameters.get_param("rsi_period", 14)
        if len(data) >= rsi_period:
            rsi = self._calculate_rsi(data["close"], rsi_period)
        else:
            rsi = 50

        # Volume confirmation
        recent_volume = data["volume"].tail(5).mean()
        avg_volume = data["volume"].tail(20).mean()
        volume_ratio = recent_volume / avg_volume if avg_volume > 0 else 1

        # Combine signals
        momentum = 0.0

        # ROC component
        if roc > 10:
            momentum += 0.4
        elif roc > 5:
            momentum += 0.2
        elif roc < -10:
            momentum -= 0.4
        elif roc < -5:
            momentum -= 0.2

        # RSI component
        if rsi > 70:
            momentum += 0.3
        elif rsi > 60:
            momentum += 0.1
        elif rsi < 30:
            momentum -= 0.3
        elif rsi < 40:
            momentum -= 0.1

        # Volume component
        volume_multiplier = self.parameters.get_param("volume_multiplier", 1.5)
        if volume_ratio > volume_multiplier:
            momentum *= 1.2
        elif volume_ratio < 0.5:
            momentum *= 0.8

        # Adjust for timeframe (shorter timeframes have less weight)
        tf_weight = {
            TimeFrame.D1: 1.0,
            TimeFrame.H4: 0.8,
            TimeFrame.H1: 0.6,
            TimeFrame.M30: 0.4,
            TimeFrame.M15: 0.3,
        }.get(timeframe, 0.5)

        return momentum * tf_weight

    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> float:
        """Calculate RSI indicator."""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        if loss.iloc[-1] == 0:
            return 100

        rs = gain.iloc[-1] / loss.iloc[-1]
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def _check_timeframe_alignment(self, momentum_scores: Dict[TimeFrame, float]) -> float:
        """Check if momentum is aligned across timeframes."""
        if len(momentum_scores) < 2:
            return 0.5

        values = list(momentum_scores.values())

        # All same sign?
        if all(v >= 0 for v in values) or all(v <= 0 for v in values):
            # Calculate consistency
            avg = sum(abs(v) for v in values) / len(values)
            min_val = min(abs(v) for v in values)

            if avg > 0:
                consistency = min_val / avg
            else:
                consistency = 0

            return min(1.0, 0.5 + consistency * 0.5)
        else:
            # Mixed signals
            positive = sum(1 for v in values if v > 0)
            ratio = positive / len(values)

            # Penalize mixed signals
            if 0.4 <= ratio <= 0.6:
                return 0.2  # Very mixed
            else:
                return 0.4  # Somewhat mixed

    async def _calculate_features(
        self, symbol: str, mtf_data: Dict[TimeFrame, pd.DataFrame]
    ) -> Dict[str, float]:
        """Calculate additional features for signal."""
        features = {}

        for tf, data in mtf_data.items():
            tf_name = tf.value

            if len(data) >= 20:
                # Moving averages
                features[f"sma20_{tf_name}"] = data["close"].rolling(20).mean().iloc[-1]
                features[f"sma50_{tf_name}"] = (
                    data["close"].rolling(50).mean().iloc[-1]
                    if len(data) >= 50 else 0
                )

                # Volatility
                features[f"volatility_{tf_name}"] = data["close"].pct_change().std() * 100

                # Volume
                features[f"volume_ratio_{tf_name}"] = (
                    data["volume"].iloc[-1] / data["volume"].rolling(20).mean().iloc[-1]
                    if data["volume"].rolling(20).mean().iloc[-1] > 0 else 1
                )

        # Get cached momentum
        if symbol in self._momentum_cache:
            for tf, momentum in self._momentum_cache[symbol].items():
                features[f"momentum_{tf.value}"] = momentum

        return features

    def _calculate_position_size(self, data: pd.DataFrame, current_price: float) -> int:
        """Calculate position size based on volatility."""
        # Calculate ATR
        if len(data) >= 14:
            high_low = data["high"] - data["low"]
            high_close = abs(data["high"] - data["close"].shift())
            low_close = abs(data["low"] - data["close"].shift())
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            atr = tr.rolling(14).mean().iloc[-1]

            # Volatility-based sizing
            volatility = atr / current_price

            # Base size
            base_size = 100

            # Adjust for volatility (inverse relationship)
            if volatility < 0.01:  # Low volatility
                return int(base_size * 1.5)
            elif volatility < 0.02:
                return base_size
            elif volatility < 0.03:
                return int(base_size * 0.75)
            else:  # High volatility
                return int(base_size * 0.5)
        else:
            return 100  # Default size

    @classmethod
    async def create(
        cls,
        db: DatabaseSessionManager,
        parameters: Optional[StrategyParameters] = None,
        **kwargs
    ) -> MultiTimeframeMomentumStrategy:
        """Factory method to create strategy instance."""
        return cls(db, parameters, **kwargs)

    async def backtest(
        self,
        symbols: List[str],
        start_date: datetime,
        end_date: datetime
    ) -> pd.DataFrame:
        """Run backtest on historical data."""
        results = []

        for symbol in symbols:
            self.parameters.symbols = [symbol]

            # Get historical data
            data = await self.get_historical_klines(
                symbol,
                self.parameters.timeframes[0],
                limit=1000,
                end_date=end_date
            )

            if data.empty:
                continue

            # Filter by date range
            mask = (data["timestamp"] >= pd.Timestamp(start_date)) & \
                   (data["timestamp"] <= pd.Timestamp(end_date))
            data = data[mask]

            # Simulate quotes and generate signals
            for idx, row in data.iterrows():
                quote = {
                    "symbol": symbol,
                    "price": row["close"],
                    "timestamp": row["timestamp"]
                }

                # Analyze for signal
                signal = await self.analyze(symbol)
                if signal:
                    results.append({
                        "timestamp": row["timestamp"],
                        "symbol": symbol,
                        "signal": signal.side,
                        "price": signal.price,
                        "strength": signal.signal_strength,
                        "stop_loss": signal.stop_loss,
                        "take_profit": signal.take_profit
                    })

        return pd.DataFrame(results)


__all__ = ["MultiTimeframeMomentumStrategy"]