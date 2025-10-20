"""Enhanced strategy base class with advanced features."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union

import pandas as pd
from loguru import logger

from longport_quant.common.types import Signal
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import (
    CalcIndicator,
    KlineDaily,
    KlineMinute,
    StrategyFeature,
)
from sqlalchemy import and_, func, select


class TimeFrame(Enum):
    """Supported timeframes for analysis."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"

    def to_minutes(self) -> int:
        """Convert timeframe to minutes."""
        mapping = {
            self.M1: 1,
            self.M5: 5,
            self.M15: 15,
            self.M30: 30,
            self.H1: 60,
            self.H4: 240,
            self.D1: 1440,
            self.W1: 10080,
        }
        return mapping[self]

    def to_seconds(self) -> int:
        """Convert timeframe to seconds."""
        return self.to_minutes() * 60


@dataclass
class StrategyParameters:
    """Strategy parameter configuration."""

    name: str
    version: str = "1.0.0"
    enabled: bool = True
    symbols: List[str] = field(default_factory=list)
    timeframes: List[TimeFrame] = field(default_factory=lambda: [TimeFrame.D1])
    lookback_periods: Dict[TimeFrame, int] = field(default_factory=dict)
    risk_per_trade: float = 0.02  # 2% risk per trade
    max_positions: int = 10
    stop_loss_atr_multiplier: float = 2.0
    take_profit_atr_multiplier: float = 3.0
    custom_params: Dict[str, Any] = field(default_factory=dict)

    def get_lookback(self, timeframe: TimeFrame) -> int:
        """Get lookback period for a timeframe."""
        return self.lookback_periods.get(timeframe, 100)

    def get_param(self, key: str, default: Any = None) -> Any:
        """Get custom parameter value."""
        return self.custom_params.get(key, default)

    def set_param(self, key: str, value: Any) -> None:
        """Set custom parameter value."""
        self.custom_params[key] = value


@dataclass
class SignalStrength:
    """Signal strength scoring."""

    base_score: float  # 0-100
    confidence: float  # 0-1
    timeframe_alignment: float  # 0-1
    volume_confirmation: float  # 0-1
    trend_strength: float  # 0-1
    risk_reward_ratio: float

    @property
    def final_score(self) -> float:
        """Calculate final signal score."""
        weights = {
            "base": 0.3,
            "confidence": 0.2,
            "alignment": 0.2,
            "volume": 0.15,
            "trend": 0.15,
        }

        score = (
            self.base_score * weights["base"]
            + self.confidence * 100 * weights["confidence"]
            + self.timeframe_alignment * 100 * weights["alignment"]
            + self.volume_confirmation * 100 * weights["volume"]
            + self.trend_strength * 100 * weights["trend"]
        )

        # Adjust for risk/reward
        if self.risk_reward_ratio > 3:
            score *= 1.2
        elif self.risk_reward_ratio < 1:
            score *= 0.8

        return min(100, max(0, score))

    @property
    def signal_quality(self) -> str:
        """Get signal quality rating."""
        score = self.final_score
        if score >= 80:
            return "EXCELLENT"
        elif score >= 60:
            return "GOOD"
        elif score >= 40:
            return "MODERATE"
        else:
            return "WEAK"


class DataAccessMixin:
    """Mixin for data access capabilities."""

    def __init__(self, db: DatabaseSessionManager):
        self._db = db

    async def get_historical_klines(
        self,
        symbol: str,
        timeframe: TimeFrame,
        limit: int = 100,
        end_date: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """Get historical kline data."""
        if end_date is None:
            end_date = datetime.now()

        async with self._db.session() as session:
            if timeframe == TimeFrame.D1:
                # Get daily klines
                stmt = (
                    select(KlineDaily)
                    .where(
                        and_(
                            KlineDaily.symbol == symbol,
                            KlineDaily.trade_date <= end_date.date(),
                        )
                    )
                    .order_by(KlineDaily.trade_date.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                klines = result.scalars().all()

                if klines:
                    df = pd.DataFrame(
                        [
                            {
                                "timestamp": pd.Timestamp(k.trade_date),
                                "open": float(k.open),
                                "high": float(k.high),
                                "low": float(k.low),
                                "close": float(k.close),
                                "volume": float(k.volume),
                                "turnover": float(k.turnover) if k.turnover else 0,
                            }
                            for k in reversed(klines)
                        ]
                    )
                    return df

            else:
                # Get minute klines
                minutes = timeframe.to_minutes()
                stmt = (
                    select(KlineMinute)
                    .where(
                        and_(
                            KlineMinute.symbol == symbol,
                            KlineMinute.timestamp <= end_date,
                            KlineMinute.interval == minutes,
                        )
                    )
                    .order_by(KlineMinute.timestamp.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                klines = result.scalars().all()

                if klines:
                    df = pd.DataFrame(
                        [
                            {
                                "timestamp": k.timestamp,
                                "open": float(k.open),
                                "high": float(k.high),
                                "low": float(k.low),
                                "close": float(k.close),
                                "volume": float(k.volume),
                                "turnover": float(k.turnover) if k.turnover else 0,
                            }
                            for k in reversed(klines)
                        ]
                    )
                    return df

        return pd.DataFrame()

    async def get_indicators(
        self,
        symbol: str,
        indicator_names: List[str],
        timeframe: TimeFrame = TimeFrame.D1,
        limit: int = 100,
    ) -> pd.DataFrame:
        """Get calculated indicators."""
        async with self._db.session() as session:
            stmt = (
                select(CalcIndicator)
                .where(
                    and_(
                        CalcIndicator.symbol == symbol,
                        CalcIndicator.indicator_name.in_(indicator_names),
                        CalcIndicator.timeframe == timeframe.value,
                    )
                )
                .order_by(CalcIndicator.timestamp.desc())
                .limit(limit * len(indicator_names))
            )
            result = await session.execute(stmt)
            indicators = result.scalars().all()

            if indicators:
                # Pivot to wide format
                data = {}
                for ind in indicators:
                    timestamp = ind.timestamp
                    if timestamp not in data:
                        data[timestamp] = {"timestamp": timestamp}
                    data[timestamp][ind.indicator_name] = float(ind.value)

                df = pd.DataFrame(list(data.values()))
                df.sort_values("timestamp", inplace=True)
                return df

        return pd.DataFrame()

    async def get_features(
        self,
        symbol: str,
        feature_names: List[str],
        limit: int = 100,
    ) -> pd.DataFrame:
        """Get strategy features."""
        async with self._db.session() as session:
            stmt = (
                select(StrategyFeature)
                .where(
                    and_(
                        StrategyFeature.symbol == symbol,
                        StrategyFeature.feature_name.in_(feature_names),
                    )
                )
                .order_by(StrategyFeature.timestamp.desc())
                .limit(limit * len(feature_names))
            )
            result = await session.execute(stmt)
            features = result.scalars().all()

            if features:
                # Pivot to wide format
                data = {}
                for feat in features:
                    timestamp = feat.timestamp
                    if timestamp not in data:
                        data[timestamp] = {"timestamp": timestamp}
                    data[timestamp][feat.feature_name] = float(feat.value)

                df = pd.DataFrame(list(data.values()))
                df.sort_values("timestamp", inplace=True)
                return df

        return pd.DataFrame()

    async def get_multi_timeframe_data(
        self,
        symbol: str,
        timeframes: List[TimeFrame],
        limit: int = 100,
    ) -> Dict[TimeFrame, pd.DataFrame]:
        """Get data for multiple timeframes."""
        data = {}
        for tf in timeframes:
            df = await self.get_historical_klines(symbol, tf, limit)
            if not df.empty:
                data[tf] = df
        return data


class EnhancedStrategyBase(ABC, DataAccessMixin):
    """Enhanced strategy base with advanced features."""

    def __init__(
        self,
        db: DatabaseSessionManager,
        parameters: Optional[StrategyParameters] = None,
        order_router: Optional[Any] = None,
        portfolio: Optional[Any] = None,
        risk_engine: Optional[Any] = None,
        signal_dispatcher: Optional[Any] = None,
    ):
        """Initialize enhanced strategy."""
        DataAccessMixin.__init__(self, db)
        self._params = parameters or StrategyParameters(name="unnamed")
        self._order_router = order_router
        self._portfolio = portfolio
        self._risk_engine = risk_engine
        self._signal_dispatcher = signal_dispatcher
        self._cached_data: Dict[str, Dict[TimeFrame, pd.DataFrame]] = {}
        self._last_update: Dict[str, datetime] = {}

    @property
    def parameters(self) -> StrategyParameters:
        """Get strategy parameters."""
        return self._params

    def update_parameters(self, params: Dict[str, Any]) -> None:
        """Update strategy parameters."""
        for key, value in params.items():
            if hasattr(self._params, key):
                setattr(self._params, key, value)
            else:
                self._params.custom_params[key] = value

        logger.info(f"Updated parameters for strategy {self._params.name}")

    async def on_start(self) -> None:
        """Hook called when strategy starts."""
        logger.info(f"Starting strategy: {self._params.name} v{self._params.version}")

        # Pre-load historical data
        for symbol in self._params.symbols:
            await self._cache_symbol_data(symbol)

    async def on_stop(self) -> None:
        """Hook called when strategy stops."""
        logger.info(f"Stopping strategy: {self._params.name}")
        self._cached_data.clear()
        self._last_update.clear()

    async def _cache_symbol_data(self, symbol: str) -> None:
        """Cache historical data for a symbol."""
        try:
            data = await self.get_multi_timeframe_data(
                symbol, self._params.timeframes, self._params.get_lookback(TimeFrame.D1)
            )
            self._cached_data[symbol] = data
            self._last_update[symbol] = datetime.now()
            logger.debug(f"Cached data for {symbol}: {len(data)} timeframes")

        except Exception as e:
            logger.error(f"Error caching data for {symbol}: {e}")

    async def get_cached_data(
        self, symbol: str, timeframe: TimeFrame
    ) -> Optional[pd.DataFrame]:
        """Get cached data for symbol and timeframe."""
        if symbol in self._cached_data and timeframe in self._cached_data[symbol]:
            # Check if cache is stale (older than timeframe interval)
            if symbol in self._last_update:
                cache_age = datetime.now() - self._last_update[symbol]
                if cache_age.total_seconds() > timeframe.to_seconds():
                    # Refresh cache
                    await self._cache_symbol_data(symbol)

            return self._cached_data.get(symbol, {}).get(timeframe)

        return None

    def calculate_signal_strength(
        self,
        symbol: str,
        signal_type: str,
        base_score: float,
        price_data: pd.DataFrame,
        indicators: Dict[str, float],
    ) -> SignalStrength:
        """Calculate comprehensive signal strength."""
        # Calculate confidence based on indicator alignment
        aligned_indicators = sum(
            1 for k, v in indicators.items() if self._is_indicator_aligned(k, v, signal_type)
        )
        confidence = aligned_indicators / len(indicators) if indicators else 0.5

        # Check timeframe alignment
        timeframe_scores = []
        for tf in self._params.timeframes:
            tf_data = self._cached_data.get(symbol, {}).get(tf)
            if tf_data is not None and len(tf_data) > 0:
                tf_score = self._calculate_timeframe_score(tf_data, signal_type)
                timeframe_scores.append(tf_score)

        timeframe_alignment = (
            sum(timeframe_scores) / len(timeframe_scores) if timeframe_scores else 0.5
        )

        # Volume confirmation
        volume_confirmation = self._calculate_volume_confirmation(price_data)

        # Trend strength
        trend_strength = self._calculate_trend_strength(price_data)

        # Risk/reward ratio
        entry_price = price_data["close"].iloc[-1]
        stop_loss = self._calculate_stop_loss(price_data, signal_type)
        take_profit = self._calculate_take_profit(price_data, signal_type)

        risk = abs(entry_price - stop_loss)
        reward = abs(take_profit - entry_price)
        risk_reward_ratio = reward / risk if risk > 0 else 0

        return SignalStrength(
            base_score=base_score,
            confidence=confidence,
            timeframe_alignment=timeframe_alignment,
            volume_confirmation=volume_confirmation,
            trend_strength=trend_strength,
            risk_reward_ratio=risk_reward_ratio,
        )

    def _is_indicator_aligned(
        self, indicator_name: str, value: float, signal_type: str
    ) -> bool:
        """Check if indicator supports the signal."""
        # Implement indicator-specific alignment logic
        if "RSI" in indicator_name:
            if signal_type == "BUY":
                return value < 30
            else:
                return value > 70

        elif "MACD" in indicator_name:
            if signal_type == "BUY":
                return value > 0
            else:
                return value < 0

        # Default: neutral
        return True

    def _calculate_timeframe_score(self, data: pd.DataFrame, signal_type: str) -> float:
        """Calculate score for a specific timeframe."""
        if len(data) < 20:
            return 0.5

        # Simple trend detection
        sma_short = data["close"].rolling(10).mean().iloc[-1]
        sma_long = data["close"].rolling(20).mean().iloc[-1]

        if signal_type == "BUY":
            return 1.0 if sma_short > sma_long else 0.0
        else:
            return 1.0 if sma_short < sma_long else 0.0

    def _calculate_volume_confirmation(self, data: pd.DataFrame) -> float:
        """Calculate volume confirmation score."""
        if len(data) < 20 or "volume" not in data.columns:
            return 0.5

        recent_vol = data["volume"].tail(5).mean()
        avg_vol = data["volume"].tail(20).mean()

        if avg_vol == 0:
            return 0.5

        vol_ratio = recent_vol / avg_vol
        # Higher volume is better confirmation
        return min(1.0, vol_ratio / 2)

    def _calculate_trend_strength(self, data: pd.DataFrame) -> float:
        """Calculate trend strength."""
        if len(data) < 20:
            return 0.5

        # Calculate ADX-like metric (simplified)
        high_low = data["high"] - data["low"]
        high_close = abs(data["high"] - data["close"].shift())
        low_close = abs(data["low"] - data["close"].shift())

        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]

        price_range = data["high"].max() - data["low"].min()
        if price_range == 0:
            return 0.5

        # Normalize ATR to 0-1 scale
        trend_strength = min(1.0, atr / price_range * 10)
        return trend_strength

    def _calculate_stop_loss(self, data: pd.DataFrame, signal_type: str) -> float:
        """Calculate stop loss price."""
        current_price = data["close"].iloc[-1]

        # Use ATR for stop loss
        if len(data) >= 14:
            high_low = data["high"] - data["low"]
            high_close = abs(data["high"] - data["close"].shift())
            low_close = abs(data["low"] - data["close"].shift())
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            atr = tr.rolling(14).mean().iloc[-1]
        else:
            atr = current_price * 0.02  # Default 2%

        if signal_type == "BUY":
            return current_price - (atr * self._params.stop_loss_atr_multiplier)
        else:
            return current_price + (atr * self._params.stop_loss_atr_multiplier)

    def _calculate_take_profit(self, data: pd.DataFrame, signal_type: str) -> float:
        """Calculate take profit price."""
        current_price = data["close"].iloc[-1]

        # Use ATR for take profit
        if len(data) >= 14:
            high_low = data["high"] - data["low"]
            high_close = abs(data["high"] - data["close"].shift())
            low_close = abs(data["low"] - data["close"].shift())
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            atr = tr.rolling(14).mean().iloc[-1]
        else:
            atr = current_price * 0.03  # Default 3%

        if signal_type == "BUY":
            return current_price + (atr * self._params.take_profit_atr_multiplier)
        else:
            return current_price - (atr * self._params.take_profit_atr_multiplier)

    async def generate_signal(
        self,
        symbol: str,
        signal_type: str,
        quantity: int,
        base_score: float = 50,
        reason: Optional[str] = None,
        features: Optional[Dict[str, float]] = None,
    ) -> Optional[Signal]:
        """Generate a signal with strength scoring."""
        try:
            # Get price data
            price_data = await self.get_cached_data(symbol, self._params.timeframes[0])
            if price_data is None or price_data.empty:
                logger.warning(f"No price data available for {symbol}")
                return None

            current_price = price_data["close"].iloc[-1]

            # Calculate signal strength
            indicators = features or {}
            strength = self.calculate_signal_strength(
                symbol, signal_type, base_score, price_data, indicators
            )

            # Check if signal meets minimum quality
            if strength.final_score < 40:
                logger.debug(
                    f"Signal for {symbol} rejected: low score {strength.final_score:.1f}"
                )
                return None

            # Calculate prices
            stop_loss = self._calculate_stop_loss(price_data, signal_type)
            take_profit = self._calculate_take_profit(price_data, signal_type)

            # Create signal
            signal = Signal(
                symbol=symbol,
                side=signal_type,
                quantity=quantity,
                price=current_price,
                signal_strength=strength.final_score / 100,
                price_target=take_profit,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy_name=self._params.name,
                reason=reason or f"Signal from {self._params.name}",
                features=features,
            )

            logger.info(
                f"Generated {strength.signal_quality} signal for {symbol}: "
                f"{signal_type} {quantity} @ {current_price:.2f} "
                f"(Score: {strength.final_score:.1f})"
            )

            return signal

        except Exception as e:
            logger.error(f"Error generating signal for {symbol}: {e}")
            return None

    async def dispatch(self, signal: Signal) -> None:
        """Dispatch signal for execution."""
        if not self._params.enabled:
            logger.debug(f"Strategy {self._params.name} is disabled, skipping signal")
            return

        if self._signal_dispatcher:
            await self._signal_dispatcher.dispatch(signal)
        elif self._order_router:
            order = {
                "symbol": signal.symbol,
                "side": signal.side,
                "quantity": signal.quantity,
                "price": signal.price_target,
            }
            await self._order_router.submit(order)
        else:
            logger.warning("No dispatcher or order router configured")

    @abstractmethod
    async def on_quote(self, quote: dict) -> None:
        """Process quote and potentially generate signals."""
        pass

    @abstractmethod
    async def analyze(self, symbol: str) -> Optional[Signal]:
        """Analyze symbol and generate signal if conditions are met."""
        pass

    @classmethod
    @abstractmethod
    async def create(
        cls,
        db: DatabaseSessionManager,
        parameters: Optional[StrategyParameters] = None,
        **kwargs,
    ) -> "EnhancedStrategyBase":
        """Factory method to create strategy instance."""
        pass


__all__ = [
    "EnhancedStrategyBase",
    "StrategyParameters",
    "TimeFrame",
    "SignalStrength",
    "DataAccessMixin",
]