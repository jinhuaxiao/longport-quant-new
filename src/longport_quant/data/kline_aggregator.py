"""K-line aggregation service for multi-timeframe data."""

from __future__ import annotations

from typing import Dict, List, Optional, Any, Literal
from datetime import datetime, date, timedelta
from dataclasses import dataclass
import pandas as pd
import asyncio

from loguru import logger
from longport_quant.persistence.db import DatabaseSessionManager
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession


TimeFrame = Literal["1m", "5m", "15m", "30m", "60m", "1d"]


@dataclass
class KlineData:
    """K-line data structure."""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover: float


class KlineAggregator:
    """Service for aggregating and querying multi-timeframe K-line data."""

    def __init__(self, db: DatabaseSessionManager):
        """
        Initialize K-line aggregator.

        Args:
            db: Database session manager
        """
        self.db = db
        self._view_map = {
            "1m": "kline_minute",
            "5m": "kline_5min",
            "15m": "kline_15min",
            "30m": "kline_30min",
            "60m": "kline_60min",
            "1d": "kline_daily"
        }

    async def get_klines(
        self,
        symbol: str,
        timeframe: TimeFrame,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000
    ) -> pd.DataFrame:
        """
        Get K-line data for specified timeframe.

        Args:
            symbol: Symbol to query
            timeframe: Timeframe (1m, 5m, 15m, 30m, 60m, 1d)
            start_time: Start time (optional)
            end_time: End time (optional)
            limit: Maximum number of records

        Returns:
            DataFrame with K-line data
        """
        table_name = self._view_map.get(timeframe)
        if not table_name:
            raise ValueError(f"Invalid timeframe: {timeframe}")

        async with self.db.session() as session:
            # Build query based on timeframe
            if timeframe == "1d":
                query = f"""
                    SELECT
                        symbol,
                        trade_date AS timestamp,
                        open,
                        high,
                        low,
                        close,
                        volume,
                        turnover
                    FROM {table_name}
                    WHERE symbol = :symbol
                """

                params = {"symbol": symbol}

                if start_time:
                    query += " AND trade_date >= :start_date"
                    params["start_date"] = start_time.date() if isinstance(start_time, datetime) else start_time

                if end_time:
                    query += " AND trade_date <= :end_date"
                    params["end_date"] = end_time.date() if isinstance(end_time, datetime) else end_time

                query += f" ORDER BY trade_date DESC LIMIT {limit}"

            else:
                query = f"""
                    SELECT
                        symbol,
                        timestamp,
                        open,
                        high,
                        low,
                        close,
                        volume,
                        turnover
                    FROM {table_name}
                    WHERE symbol = :symbol
                """

                params = {"symbol": symbol}

                if start_time:
                    query += " AND timestamp >= :start_time"
                    params["start_time"] = start_time

                if end_time:
                    query += " AND timestamp <= :end_time"
                    params["end_time"] = end_time

                query += f" ORDER BY timestamp DESC LIMIT {limit}"

            # Execute query
            result = await session.execute(text(query), params)
            rows = result.fetchall()

            if not rows:
                logger.debug(f"No {timeframe} K-line data found for {symbol}")
                return pd.DataFrame()

            # Convert to DataFrame
            df = pd.DataFrame(rows)
            df.columns = ['symbol', 'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover']

            # Convert types
            for col in ['open', 'high', 'low', 'close', 'turnover']:
                df[col] = df[col].astype(float)

            df['volume'] = df['volume'].astype(int)
            df.set_index('timestamp', inplace=True)
            df.sort_index(inplace=True)

            logger.debug(f"Retrieved {len(df)} {timeframe} K-lines for {symbol}")
            return df

    async def get_multi_timeframe(
        self,
        symbol: str,
        timeframes: List[TimeFrame],
        periods: int = 100
    ) -> Dict[str, pd.DataFrame]:
        """
        Get K-line data for multiple timeframes.

        Args:
            symbol: Symbol to query
            timeframes: List of timeframes
            periods: Number of periods for each timeframe

        Returns:
            Dictionary mapping timeframe to DataFrame
        """
        tasks = []
        for tf in timeframes:
            task = self.get_klines(symbol, tf, limit=periods)
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        data = {}
        for tf, result in zip(timeframes, results):
            if isinstance(result, Exception):
                logger.error(f"Error getting {tf} data: {result}")
                data[tf] = pd.DataFrame()
            else:
                data[tf] = result

        return data

    async def refresh_views(self, timeframes: Optional[List[TimeFrame]] = None):
        """
        Manually refresh materialized views.

        Args:
            timeframes: Specific timeframes to refresh (None = all)
        """
        if timeframes is None:
            timeframes = ["5m", "15m", "30m", "60m"]

        async with self.db.session() as session:
            for tf in timeframes:
                view_name = self._view_map.get(tf)
                if view_name and view_name != "kline_minute" and view_name != "kline_daily":
                    try:
                        await session.execute(
                            text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view_name}")
                        )
                        logger.info(f"Refreshed materialized view: {view_name}")
                    except Exception as e:
                        logger.error(f"Error refreshing {view_name}: {e}")

            await session.commit()

    async def get_view_stats(self) -> pd.DataFrame:
        """
        Get statistics about materialized views.

        Returns:
            DataFrame with view statistics
        """
        async with self.db.session() as session:
            query = """
                SELECT
                    view_name,
                    last_refresh,
                    refresh_duration,
                    row_count,
                    pg_size_pretty(size_bytes) AS size,
                    EXTRACT(EPOCH FROM (NOW() - last_refresh))/60 AS minutes_since_refresh
                FROM materialized_view_stats
                ORDER BY view_name
            """

            result = await session.execute(text(query))
            rows = result.fetchall()

            if not rows:
                logger.warning("No materialized view statistics available")
                return pd.DataFrame()

            df = pd.DataFrame(rows)
            df.columns = ['view_name', 'last_refresh', 'refresh_duration',
                         'row_count', 'size', 'minutes_since_refresh']

            return df

    async def aggregate_custom(
        self,
        symbol: str,
        base_timeframe: TimeFrame,
        target_minutes: int,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> pd.DataFrame:
        """
        Create custom timeframe aggregation.

        Args:
            symbol: Symbol to aggregate
            base_timeframe: Base timeframe to aggregate from
            target_minutes: Target timeframe in minutes
            start_time: Start time
            end_time: End time

        Returns:
            Aggregated DataFrame
        """
        # Get base data
        base_data = await self.get_klines(
            symbol, base_timeframe, start_time, end_time
        )

        if base_data.empty:
            return pd.DataFrame()

        # Reset index to work with timestamp
        base_data = base_data.reset_index()

        # Create aggregation rule
        rule = f"{target_minutes}T" if target_minutes < 1440 else f"{target_minutes//1440}D"

        # Aggregate
        aggregated = base_data.set_index('timestamp').resample(rule).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum',
            'turnover': 'sum'
        })

        # Remove NaN rows
        aggregated = aggregated.dropna()

        logger.debug(f"Aggregated {len(base_data)} {base_timeframe} bars into "
                    f"{len(aggregated)} {target_minutes}-minute bars")

        return aggregated

    async def calculate_indicators(
        self,
        symbol: str,
        timeframe: TimeFrame,
        indicators: List[str],
        periods: int = 100
    ) -> pd.DataFrame:
        """
        Calculate technical indicators on K-line data.

        Args:
            symbol: Symbol
            timeframe: Timeframe
            indicators: List of indicators to calculate
            periods: Number of periods to fetch

        Returns:
            DataFrame with K-line data and indicators
        """
        from longport_quant.features.technical_indicators import TechnicalIndicators

        # Get K-line data
        df = await self.get_klines(symbol, timeframe, limit=periods)

        if df.empty:
            return df

        # Calculate requested indicators
        if 'sma_20' in indicators:
            df['sma_20'] = TechnicalIndicators.sma(df['close'].values, 20)

        if 'ema_12' in indicators:
            df['ema_12'] = TechnicalIndicators.ema(df['close'].values, 12)

        if 'rsi_14' in indicators:
            df['rsi_14'] = TechnicalIndicators.rsi(df['close'].values, 14)

        if 'macd' in indicators:
            macd_result = TechnicalIndicators.macd(df['close'].values)
            df['macd'] = macd_result['macd']
            df['macd_signal'] = macd_result['signal']
            df['macd_histogram'] = macd_result['histogram']

        if 'bollinger' in indicators:
            bb_result = TechnicalIndicators.bollinger_bands(df['close'].values)
            df['bb_upper'] = bb_result['upper']
            df['bb_middle'] = bb_result['middle']
            df['bb_lower'] = bb_result['lower']

        if 'volume_ratio' in indicators:
            df['volume_sma'] = TechnicalIndicators.sma(df['volume'].values, 20)
            df['volume_ratio'] = df['volume'] / df['volume_sma']

        return df

    async def find_patterns(
        self,
        symbol: str,
        timeframe: TimeFrame,
        patterns: List[str],
        periods: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Find chart patterns in K-line data.

        Args:
            symbol: Symbol
            timeframe: Timeframe
            patterns: List of patterns to find
            periods: Number of periods to analyze

        Returns:
            List of found patterns
        """
        df = await self.get_klines(symbol, timeframe, limit=periods)

        if df.empty:
            return []

        found_patterns = []

        # Detect requested patterns
        if 'doji' in patterns:
            doji_signals = self._detect_doji(df)
            found_patterns.extend(doji_signals)

        if 'hammer' in patterns:
            hammer_signals = self._detect_hammer(df)
            found_patterns.extend(hammer_signals)

        if 'engulfing' in patterns:
            engulfing_signals = self._detect_engulfing(df)
            found_patterns.extend(engulfing_signals)

        if 'triangle' in patterns:
            triangle_signals = self._detect_triangle(df)
            found_patterns.extend(triangle_signals)

        logger.debug(f"Found {len(found_patterns)} patterns in {symbol} {timeframe}")
        return found_patterns

    def _detect_doji(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Detect doji patterns."""
        patterns = []

        for i in range(1, len(df)):
            body = abs(df.iloc[i]['close'] - df.iloc[i]['open'])
            range_hl = df.iloc[i]['high'] - df.iloc[i]['low']

            if range_hl > 0 and body / range_hl < 0.1:
                patterns.append({
                    'pattern': 'doji',
                    'timestamp': df.index[i],
                    'price': df.iloc[i]['close'],
                    'strength': 1 - (body / range_hl) * 10
                })

        return patterns

    def _detect_hammer(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Detect hammer patterns."""
        patterns = []

        for i in range(1, len(df)):
            body = abs(df.iloc[i]['close'] - df.iloc[i]['open'])
            range_hl = df.iloc[i]['high'] - df.iloc[i]['low']
            lower_shadow = min(df.iloc[i]['close'], df.iloc[i]['open']) - df.iloc[i]['low']
            upper_shadow = df.iloc[i]['high'] - max(df.iloc[i]['close'], df.iloc[i]['open'])

            if (range_hl > 0 and
                body / range_hl < 0.3 and
                lower_shadow > body * 2 and
                upper_shadow < body * 0.3):

                patterns.append({
                    'pattern': 'hammer',
                    'timestamp': df.index[i],
                    'price': df.iloc[i]['close'],
                    'strength': lower_shadow / body
                })

        return patterns

    def _detect_engulfing(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Detect engulfing patterns."""
        patterns = []

        for i in range(1, len(df)):
            curr_body = df.iloc[i]['close'] - df.iloc[i]['open']
            prev_body = df.iloc[i-1]['close'] - df.iloc[i-1]['open']

            # Bullish engulfing
            if (prev_body < 0 and curr_body > 0 and
                df.iloc[i]['open'] < df.iloc[i-1]['close'] and
                df.iloc[i]['close'] > df.iloc[i-1]['open']):

                patterns.append({
                    'pattern': 'bullish_engulfing',
                    'timestamp': df.index[i],
                    'price': df.iloc[i]['close'],
                    'strength': abs(curr_body / prev_body)
                })

            # Bearish engulfing
            elif (prev_body > 0 and curr_body < 0 and
                  df.iloc[i]['open'] > df.iloc[i-1]['close'] and
                  df.iloc[i]['close'] < df.iloc[i-1]['open']):

                patterns.append({
                    'pattern': 'bearish_engulfing',
                    'timestamp': df.index[i],
                    'price': df.iloc[i]['close'],
                    'strength': abs(curr_body / prev_body)
                })

        return patterns

    def _detect_triangle(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Detect triangle patterns (simplified)."""
        patterns = []

        if len(df) < 20:
            return patterns

        # Check for converging highs and lows
        recent = df.iloc[-20:]
        highs = recent['high'].values
        lows = recent['low'].values

        # Simple linear regression to check convergence
        import numpy as np
        x = np.arange(len(highs))

        high_slope = np.polyfit(x, highs, 1)[0]
        low_slope = np.polyfit(x, lows, 1)[0]

        # Ascending triangle: flat top, rising bottom
        if abs(high_slope) < 0.001 and low_slope > 0.001:
            patterns.append({
                'pattern': 'ascending_triangle',
                'timestamp': df.index[-1],
                'price': df.iloc[-1]['close'],
                'strength': low_slope * 100
            })

        # Descending triangle: declining top, flat bottom
        elif high_slope < -0.001 and abs(low_slope) < 0.001:
            patterns.append({
                'pattern': 'descending_triangle',
                'timestamp': df.index[-1],
                'price': df.iloc[-1]['close'],
                'strength': abs(high_slope) * 100
            })

        # Symmetrical triangle: converging lines
        elif high_slope < -0.001 and low_slope > 0.001:
            patterns.append({
                'pattern': 'symmetrical_triangle',
                'timestamp': df.index[-1],
                'price': df.iloc[-1]['close'],
                'strength': (abs(high_slope) + low_slope) * 50
            })

        return patterns