"""Feature calculation engine for trading strategies."""

from __future__ import annotations

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
import pandas as pd
import numpy as np
from collections import defaultdict
import asyncio

from loguru import logger
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import (
    KlineDaily, KlineMinute, RealtimeQuote,
    MarketDepth, CalcIndicator, StrategyFeature
)
from longport_quant.features.technical_indicators import TechnicalIndicators
from sqlalchemy import select, and_, delete
from sqlalchemy.dialects.postgresql import insert


@dataclass
class FeatureConfig:
    """Feature calculation configuration."""

    # Price features
    calculate_returns: bool = True
    return_periods: List[int] = field(default_factory=lambda: [1, 5, 10, 20])

    # Momentum features
    calculate_momentum: bool = True
    momentum_periods: List[int] = field(default_factory=lambda: [5, 10, 20])

    # Volatility features
    calculate_volatility: bool = True
    volatility_window: int = 20

    # Volume features
    calculate_volume_features: bool = True
    volume_window: int = 20

    # Microstructure features
    calculate_microstructure: bool = True
    depth_levels: int = 5

    # Technical indicators
    calculate_technical: bool = True
    technical_params: Dict[str, Any] = field(default_factory=dict)

    # Storage settings
    store_to_db: bool = True
    cache_enabled: bool = True
    cache_ttl: int = 3600  # seconds


class FeatureEngine:
    """Engine for calculating and managing trading features."""

    def __init__(self, db: DatabaseSessionManager, config: Optional[FeatureConfig] = None):
        """
        Initialize feature engine.

        Args:
            db: Database session manager
            config: Feature configuration
        """
        self.db = db
        self.config = config or FeatureConfig()
        self._cache: Dict[str, pd.DataFrame] = {}
        self._cache_timestamps: Dict[str, datetime] = {}

    async def calculate_features(
        self,
        symbol: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        data_frequency: str = 'daily'
    ) -> pd.DataFrame:
        """
        Calculate all features for a symbol.

        Args:
            symbol: Symbol to calculate features for
            start_date: Start date for calculation
            end_date: End date for calculation
            data_frequency: 'daily' or 'minute'

        Returns:
            DataFrame with calculated features
        """
        logger.info(f"Calculating features for {symbol} from {start_date} to {end_date}")

        # Check cache
        cache_key = f"{symbol}_{data_frequency}_{start_date}_{end_date}"
        if self.config.cache_enabled and cache_key in self._cache:
            if self._is_cache_valid(cache_key):
                logger.debug(f"Using cached features for {symbol}")
                return self._cache[cache_key]

        # Load market data
        df = await self._load_market_data(symbol, start_date, end_date, data_frequency)

        if df is None or df.empty:
            logger.warning(f"No data available for {symbol}")
            return pd.DataFrame()

        # Calculate features
        features = pd.DataFrame(index=df.index)

        # Price features
        if self.config.calculate_returns:
            price_features = self._calculate_price_features(df)
            features = pd.concat([features, price_features], axis=1)

        # Momentum features
        if self.config.calculate_momentum:
            momentum_features = self._calculate_momentum_features(df)
            features = pd.concat([features, momentum_features], axis=1)

        # Volatility features
        if self.config.calculate_volatility:
            volatility_features = self._calculate_volatility_features(df)
            features = pd.concat([features, volatility_features], axis=1)

        # Volume features
        if self.config.calculate_volume_features:
            volume_features = self._calculate_volume_features(df)
            features = pd.concat([features, volume_features], axis=1)

        # Technical indicators
        if self.config.calculate_technical:
            technical_features = self._calculate_technical_features(df)
            features = pd.concat([features, technical_features], axis=1)

        # Microstructure features (if available)
        if self.config.calculate_microstructure:
            microstructure_features = await self._calculate_microstructure_features(
                symbol, df.index[0], df.index[-1]
            )
            if not microstructure_features.empty:
                features = pd.concat([features, microstructure_features], axis=1)

        # Store to database
        if self.config.store_to_db:
            await self._store_features(symbol, features)

        # Update cache
        if self.config.cache_enabled:
            self._cache[cache_key] = features
            self._cache_timestamps[cache_key] = datetime.now()

        logger.info(f"Calculated {len(features.columns)} features for {symbol}")
        return features

    def _calculate_price_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate price-based features."""
        features = pd.DataFrame(index=df.index)

        # Returns
        for period in self.config.return_periods:
            features[f'return_{period}d'] = df['close'].pct_change(period)
            features[f'log_return_{period}d'] = np.log(df['close'] / df['close'].shift(period))

        # Price position
        features['price_to_high'] = df['close'] / df['high'].rolling(20).max()
        features['price_to_low'] = df['close'] / df['low'].rolling(20).min()

        # Price patterns
        features['higher_high'] = (df['high'] > df['high'].shift(1)).astype(int)
        features['lower_low'] = (df['low'] < df['low'].shift(1)).astype(int)
        features['inside_bar'] = ((df['high'] < df['high'].shift(1)) &
                                  (df['low'] > df['low'].shift(1))).astype(int)

        # Gap analysis
        features['gap_up'] = ((df['low'] > df['high'].shift(1))).astype(int)
        features['gap_down'] = ((df['high'] < df['low'].shift(1))).astype(int)

        # Candlestick patterns
        features['doji'] = (abs(df['open'] - df['close']) / (df['high'] - df['low']) < 0.1).astype(int)
        features['hammer'] = self._detect_hammer(df)
        features['shooting_star'] = self._detect_shooting_star(df)

        return features

    def _calculate_momentum_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate momentum-based features."""
        features = pd.DataFrame(index=df.index)

        # Rate of change
        for period in self.config.momentum_periods:
            features[f'roc_{period}d'] = (df['close'] - df['close'].shift(period)) / df['close'].shift(period)

        # Relative strength
        features['rs_20d'] = df['close'] / df['close'].rolling(20).mean()

        # Price acceleration
        returns = df['close'].pct_change()
        features['acceleration'] = returns - returns.shift(1)

        # Efficiency ratio
        for period in [10, 20]:
            direction = abs(df['close'] - df['close'].shift(period))
            volatility = (df['close'].diff().abs()).rolling(period).sum()
            features[f'efficiency_{period}d'] = direction / volatility

        # Time series momentum
        features['tsmom_12m'] = df['close'] / df['close'].shift(252) - 1

        return features

    def _calculate_volatility_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate volatility-based features."""
        features = pd.DataFrame(index=df.index)

        returns = df['close'].pct_change()

        # Historical volatility
        features['volatility_20d'] = returns.rolling(20).std() * np.sqrt(252)
        features['volatility_60d'] = returns.rolling(60).std() * np.sqrt(252)

        # Parkinson volatility (using high-low)
        features['parkinson_vol'] = np.sqrt(
            (np.log(df['high'] / df['low']) ** 2).rolling(20).mean() * 252 / (4 * np.log(2))
        )

        # Garman-Klass volatility
        features['gk_vol'] = self._garman_klass_volatility(df)

        # Average True Range (ATR)
        features['atr_14'] = TechnicalIndicators.atr(
            df['high'].values, df['low'].values, df['close'].values, 14
        )
        features['atr_normalized'] = features['atr_14'] / df['close']

        # Volatility ratio
        features['vol_ratio'] = features['volatility_20d'] / features['volatility_60d']

        # Volatility of volatility
        features['vol_of_vol'] = features['volatility_20d'].rolling(20).std()

        return features

    def _calculate_volume_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate volume-based features."""
        features = pd.DataFrame(index=df.index)

        # Volume moving averages
        features['volume_ma_20'] = df['volume'].rolling(20).mean()
        features['volume_ratio'] = df['volume'] / features['volume_ma_20']

        # On-Balance Volume (OBV)
        features['obv'] = TechnicalIndicators.obv(df['close'].values, df['volume'].values)
        features['obv_ma'] = features['obv'].rolling(20).mean()

        # Volume Rate of Change
        features['vroc'] = df['volume'].pct_change(10)

        # Money Flow
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        money_flow = typical_price * df['volume']
        features['money_flow_20d'] = money_flow.rolling(20).sum()

        # Accumulation/Distribution Line
        clv = ((df['close'] - df['low']) - (df['high'] - df['close'])) / (df['high'] - df['low'])
        clv = clv.fillna(0)
        features['adl'] = (clv * df['volume']).cumsum()

        # Volume-Weighted Average Price (VWAP)
        features['vwap'] = (typical_price * df['volume']).rolling(20).sum() / df['volume'].rolling(20).sum()
        features['price_to_vwap'] = df['close'] / features['vwap']

        # Force Index
        features['force_index'] = df['close'].diff() * df['volume']

        return features

    def _calculate_technical_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicator features."""
        features = pd.DataFrame(index=df.index)

        # Moving averages
        for period in [5, 10, 20, 50, 200]:
            ma = TechnicalIndicators.sma(df['close'].values, period)
            features[f'ma_{period}'] = ma
            features[f'price_to_ma_{period}'] = df['close'] / ma

        # MACD
        macd_result = TechnicalIndicators.macd(df['close'].values)
        features['macd'] = macd_result['macd']
        features['macd_signal'] = macd_result['signal']
        features['macd_histogram'] = macd_result['histogram']

        # RSI
        features['rsi_14'] = TechnicalIndicators.rsi(df['close'].values, 14)
        features['rsi_oversold'] = (features['rsi_14'] < 30).astype(int)
        features['rsi_overbought'] = (features['rsi_14'] > 70).astype(int)

        # Stochastic
        stoch_result = TechnicalIndicators.stochastic(
            df['high'].values, df['low'].values, df['close'].values
        )
        features['stoch_k'] = stoch_result['k']
        features['stoch_d'] = stoch_result['d']

        # Bollinger Bands
        bb_result = TechnicalIndicators.bollinger_bands(df['close'].values)
        features['bb_upper'] = bb_result['upper']
        features['bb_middle'] = bb_result['middle']
        features['bb_lower'] = bb_result['lower']
        features['bb_width'] = bb_result['width']
        features['bb_percent'] = bb_result['percent']

        # Williams %R
        features['williams_r'] = TechnicalIndicators.williams_r(
            df['high'].values, df['low'].values, df['close'].values
        )

        return features

    async def _calculate_microstructure_features(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime
    ) -> pd.DataFrame:
        """Calculate market microstructure features."""
        features = pd.DataFrame()

        try:
            async with self.db.session() as session:
                # Load market depth data
                stmt = select(MarketDepth).where(
                    and_(
                        MarketDepth.symbol == symbol,
                        MarketDepth.timestamp >= start_time,
                        MarketDepth.timestamp <= end_time
                    )
                ).order_by(MarketDepth.timestamp, MarketDepth.position)

                result = await session.execute(stmt)
                depth_data = result.scalars().all()

                if not depth_data:
                    return features

                # Process depth data by timestamp
                depth_by_time = defaultdict(list)
                for d in depth_data:
                    depth_by_time[d.timestamp].append(d)

                # Calculate features for each timestamp
                feature_list = []
                for timestamp, depths in depth_by_time.items():
                    feature_dict = {'timestamp': timestamp}

                    # Separate bid and ask
                    bids = [d for d in depths if d.side == 'BID']
                    asks = [d for d in depths if d.side == 'ASK']

                    if bids and asks:
                        # Spread
                        best_bid = max(bids, key=lambda x: x.price)
                        best_ask = min(asks, key=lambda x: x.price)
                        spread = float(best_ask.price - best_bid.price)
                        mid_price = float((best_ask.price + best_bid.price) / 2)

                        feature_dict['spread'] = spread
                        feature_dict['spread_pct'] = spread / mid_price

                        # Depth imbalance
                        bid_volume = sum(b.volume for b in bids[:self.config.depth_levels])
                        ask_volume = sum(a.volume for a in asks[:self.config.depth_levels])
                        total_volume = bid_volume + ask_volume

                        if total_volume > 0:
                            feature_dict['depth_imbalance'] = (bid_volume - ask_volume) / total_volume

                        # Weighted mid price
                        if bid_volume + ask_volume > 0:
                            weighted_mid = (
                                float(best_bid.price) * ask_volume +
                                float(best_ask.price) * bid_volume
                            ) / (bid_volume + ask_volume)
                            feature_dict['weighted_mid_price'] = weighted_mid

                        # Order book slope
                        if len(bids) >= 2 and len(asks) >= 2:
                            bid_slope = self._calculate_book_slope(bids[:5])
                            ask_slope = self._calculate_book_slope(asks[:5])
                            feature_dict['bid_slope'] = bid_slope
                            feature_dict['ask_slope'] = ask_slope

                    feature_list.append(feature_dict)

                if feature_list:
                    features = pd.DataFrame(feature_list)
                    features.set_index('timestamp', inplace=True)

        except Exception as e:
            logger.error(f"Error calculating microstructure features: {e}")

        return features

    def _calculate_book_slope(self, orders: List[Any]) -> float:
        """Calculate order book slope."""
        if len(orders) < 2:
            return 0.0

        prices = [float(o.price) for o in orders]
        volumes = [o.volume for o in orders]
        cum_volumes = np.cumsum(volumes)

        # Linear regression of price vs cumulative volume
        if len(prices) > 1:
            coefficients = np.polyfit(cum_volumes, prices, 1)
            return coefficients[0]

        return 0.0

    def _garman_klass_volatility(self, df: pd.DataFrame, period: int = 20) -> pd.Series:
        """Calculate Garman-Klass volatility."""
        log_hl = np.log(df['high'] / df['low']) ** 2
        log_oc = np.log(df['close'] / df['open']) ** 2

        gk = np.sqrt(
            (0.5 * log_hl - (2 * np.log(2) - 1) * log_oc).rolling(period).mean() * 252
        )

        return gk

    def _detect_hammer(self, df: pd.DataFrame) -> pd.Series:
        """Detect hammer candlestick pattern."""
        body = abs(df['close'] - df['open'])
        range_hl = df['high'] - df['low']
        upper_shadow = df['high'] - df[['close', 'open']].max(axis=1)
        lower_shadow = df[['close', 'open']].min(axis=1) - df['low']

        hammer = (
            (body / range_hl < 0.3) &
            (lower_shadow > 2 * body) &
            (upper_shadow < body * 0.3)
        ).astype(int)

        return hammer

    def _detect_shooting_star(self, df: pd.DataFrame) -> pd.Series:
        """Detect shooting star candlestick pattern."""
        body = abs(df['close'] - df['open'])
        range_hl = df['high'] - df['low']
        upper_shadow = df['high'] - df[['close', 'open']].max(axis=1)
        lower_shadow = df[['close', 'open']].min(axis=1) - df['low']

        shooting_star = (
            (body / range_hl < 0.3) &
            (upper_shadow > 2 * body) &
            (lower_shadow < body * 0.3)
        ).astype(int)

        return shooting_star

    async def _load_market_data(
        self,
        symbol: str,
        start_date: Optional[date],
        end_date: Optional[date],
        frequency: str
    ) -> Optional[pd.DataFrame]:
        """Load market data from database."""
        async with self.db.session() as session:
            if frequency == 'daily':
                stmt = select(KlineDaily).where(
                    and_(
                        KlineDaily.symbol == symbol,
                        KlineDaily.trade_date >= start_date if start_date else True,
                        KlineDaily.trade_date <= end_date if end_date else True
                    )
                ).order_by(KlineDaily.trade_date)

                result = await session.execute(stmt)
                klines = result.scalars().all()

                if klines:
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
                    df.set_index('timestamp', inplace=True)
                    return df

            elif frequency == 'minute':
                stmt = select(KlineMinute).where(
                    and_(
                        KlineMinute.symbol == symbol,
                        KlineMinute.timestamp >= datetime.combine(start_date, datetime.min.time()) if start_date else True,
                        KlineMinute.timestamp <= datetime.combine(end_date, datetime.max.time()) if end_date else True
                    )
                ).order_by(KlineMinute.timestamp)

                result = await session.execute(stmt)
                klines = result.scalars().all()

                if klines:
                    df = pd.DataFrame([
                        {
                            'timestamp': k.timestamp,
                            'open': float(k.open),
                            'high': float(k.high),
                            'low': float(k.low),
                            'close': float(k.close),
                            'volume': k.volume
                        }
                        for k in klines
                    ])
                    df.set_index('timestamp', inplace=True)
                    return df

        return None

    async def _store_features(self, symbol: str, features: pd.DataFrame) -> None:
        """Store calculated features to database."""
        if features.empty:
            return

        try:
            async with self.db.session() as session:
                # Delete existing features for the same period
                if not features.index.empty:
                    min_time = features.index.min()
                    max_time = features.index.max()

                    delete_stmt = delete(StrategyFeature).where(
                        and_(
                            StrategyFeature.symbol == symbol,
                            StrategyFeature.timestamp >= min_time,
                            StrategyFeature.timestamp <= max_time
                        )
                    )
                    await session.execute(delete_stmt)

                # Prepare feature records
                records = []
                for timestamp, row in features.iterrows():
                    for feature_name, value in row.items():
                        if pd.notna(value):
                            records.append({
                                'symbol': symbol,
                                'timestamp': timestamp,
                                'feature_name': feature_name,
                                'value': float(value),
                                'meta_data': {}
                            })

                # Batch insert
                if records:
                    # Insert in batches of 1000
                    batch_size = 1000
                    for i in range(0, len(records), batch_size):
                        batch = records[i:i + batch_size]
                        stmt = insert(StrategyFeature).values(batch)
                        stmt = stmt.on_conflict_do_update(
                            index_elements=['symbol', 'timestamp', 'feature_name'],
                            set_={'value': stmt.excluded.value}
                        )
                        await session.execute(stmt)

                    await session.commit()
                    logger.info(f"Stored {len(records)} feature values for {symbol}")

        except Exception as e:
            logger.error(f"Error storing features: {e}")

    def _is_cache_valid(self, cache_key: str) -> bool:
        """Check if cache is still valid."""
        if cache_key not in self._cache_timestamps:
            return False

        age = (datetime.now() - self._cache_timestamps[cache_key]).total_seconds()
        return age < self.config.cache_ttl

    async def calculate_batch_features(
        self,
        symbols: List[str],
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> Dict[str, pd.DataFrame]:
        """
        Calculate features for multiple symbols in batch.

        Args:
            symbols: List of symbols
            start_date: Start date
            end_date: End date

        Returns:
            Dictionary mapping symbols to feature DataFrames
        """
        results = {}

        # Process in parallel
        tasks = []
        for symbol in symbols:
            task = self.calculate_features(symbol, start_date, end_date)
            tasks.append(task)

        feature_dfs = await asyncio.gather(*tasks, return_exceptions=True)

        for symbol, feature_df in zip(symbols, feature_dfs):
            if isinstance(feature_df, Exception):
                logger.error(f"Error calculating features for {symbol}: {feature_df}")
                results[symbol] = pd.DataFrame()
            else:
                results[symbol] = feature_df

        return results