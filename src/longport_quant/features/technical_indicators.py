"""Technical indicators calculation engine."""

from __future__ import annotations

from collections.abc import Mapping, Sequence as SequenceCollection
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np
import pandas as pd
from loguru import logger

try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    TALIB_AVAILABLE = False
    logger.warning("TA-Lib not available, falling back to numpy implementations")


@dataclass
class IndicatorBatchRequest:
    """Batch calculation request for technical indicators."""

    symbol: str
    data: Union[pd.DataFrame, Sequence[Dict[str, Any]]]
    indicators: Optional[List[str]] = None
    price_col: str = 'close'
    high_col: str = 'high'
    low_col: str = 'low'
    volume_col: str = 'volume'
    coerce_numeric: bool = True
    sort_by: Optional[str] = 'timestamp'


def _ensure_dataframe(data: Union[pd.DataFrame, Sequence[Dict[str, Any]]]) -> pd.DataFrame:
    """Return a defensive DataFrame copy from various input formats."""

    if isinstance(data, pd.DataFrame):
        return data.copy()

    if isinstance(data, pd.Series):
        return data.to_frame().T.reset_index(drop=True)

    if isinstance(data, SequenceCollection) and not isinstance(data, (str, bytes)):
        records = list(data)
        if not records:
            return pd.DataFrame()

        # Allow list of dict-like objects
        if isinstance(records[0], Mapping):
            return pd.DataFrame(records)

        # Allow list of named tuples or simple sequences with consistent length
        if isinstance(records[0], SequenceCollection) and not isinstance(records[0], (str, bytes)):
            return pd.DataFrame(records)

    raise TypeError(f"Unsupported data type for indicator calculation: {type(data)!r}")


def _coerce_numeric_columns(df: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    """Coerce selected columns to numeric values in-place."""

    for column in columns:
        if column and column in df.columns:
            df[column] = pd.to_numeric(df[column], errors='coerce')

    return df


def _normalize_indicator_list(value: Optional[Sequence[str]]) -> Optional[List[str]]:
    """
    Normalize indicator selections to a list of strings.

    Strings are preserved in their original case; casing is harmonized during
    filtering to keep this helper side-effect free for callers.
    """

    if value is None:
        return None

    if isinstance(value, str):
        return [value]

    return [str(item) for item in value]


def _default_base_columns(df: pd.DataFrame, request: IndicatorBatchRequest) -> List[str]:
    """Determine base columns to retain alongside indicators."""

    ordered = []
    seen = set()
    for column in [
        'timestamp',
        'symbol',
        'open',
        request.high_col,
        request.low_col,
        request.price_col,
        request.volume_col,
    ]:
        if column and column in df.columns and column not in seen:
            ordered.append(column)
            seen.add(column)

    return ordered


def _filter_indicator_columns(
    df: pd.DataFrame,
    request: IndicatorBatchRequest,
) -> pd.DataFrame:
    """Filter DataFrame to base columns and requested indicator columns."""

    if not request.indicators:
        return df

    requested = [indicator.lower() for indicator in request.indicators]
    base_columns = _default_base_columns(df, request)

    selected = []
    for column in df.columns:
        if column in base_columns:
            continue

        lowered = column.lower()
        if lowered in requested or any(indicator in lowered for indicator in requested):
            selected.append(column)

    # Ensure deterministic order without duplicates
    ordered = []
    seen = set()
    for column in base_columns + selected:
        if column in df.columns and column not in seen:
            ordered.append(column)
            seen.add(column)

    return df.loc[:, ordered]


class TechnicalIndicators:
    """Technical indicators calculation engine."""

    @staticmethod
    def sma(prices: Union[List[float], np.ndarray, pd.Series], period: int) -> np.ndarray:
        """
        Simple Moving Average.

        Args:
            prices: Price series
            period: Number of periods

        Returns:
            Array of SMA values
        """
        if isinstance(prices, list):
            prices = np.array(prices, dtype=float)
        elif isinstance(prices, pd.Series):
            prices = prices.values

        if len(prices) < period:
            return np.full(len(prices), np.nan)

        sma = np.full(len(prices), np.nan)
        sma[period-1:] = np.convolve(prices, np.ones(period)/period, mode='valid')
        return sma

    @staticmethod
    def ema(prices: Union[List[float], np.ndarray, pd.Series], period: int) -> np.ndarray:
        """
        Exponential Moving Average.

        Args:
            prices: Price series
            period: Number of periods

        Returns:
            Array of EMA values
        """
        if isinstance(prices, list):
            prices = np.array(prices, dtype=float)
        elif isinstance(prices, pd.Series):
            prices = prices.values

        if len(prices) < period:
            return np.full(len(prices), np.nan)

        # Use TA-Lib if available (faster and more accurate)
        if TALIB_AVAILABLE:
            try:
                return talib.EMA(prices, timeperiod=period)
            except Exception as e:
                logger.debug(f"TA-Lib EMA failed, falling back to numpy: {e}")

        # Fallback to numpy implementation
        alpha = 2 / (period + 1)
        ema = np.full(len(prices), np.nan)

        # Find first valid index with enough non-NaN values for initial SMA
        valid_data = ~np.isnan(prices)
        if valid_data.sum() < period:
            return ema  # Not enough valid data

        # Find starting index where we have 'period' consecutive valid values
        start_idx = None
        for i in range(len(prices) - period + 1):
            if all(valid_data[i:i+period]):
                start_idx = i + period - 1
                break

        if start_idx is None:
            return ema  # Cannot find enough consecutive valid values

        # Initialize with SMA of first 'period' valid values
        ema[start_idx] = np.mean(prices[start_idx-period+1:start_idx+1])

        # Calculate EMA for remaining values
        for i in range(start_idx + 1, len(prices)):
            if np.isnan(prices[i]):
                continue  # Skip NaN values but keep EMA chain
            if np.isnan(ema[i-1]):
                # If previous EMA is NaN, try to find last valid EMA
                last_valid = start_idx
                for j in range(i-1, start_idx, -1):
                    if not np.isnan(ema[j]):
                        last_valid = j
                        break
                if not np.isnan(ema[last_valid]):
                    ema[i] = alpha * prices[i] + (1 - alpha) * ema[last_valid]
            else:
                ema[i] = alpha * prices[i] + (1 - alpha) * ema[i-1]

        return ema

    @staticmethod
    def macd(prices: Union[List[float], np.ndarray, pd.Series],
             fast_period: int = 12,
             slow_period: int = 26,
             signal_period: int = 9) -> Dict[str, np.ndarray]:
        """
        MACD (Moving Average Convergence Divergence).

        Args:
            prices: Price series
            fast_period: Fast EMA period (default: 12)
            slow_period: Slow EMA period (default: 26)
            signal_period: Signal EMA period (default: 9)

        Returns:
            Dictionary with 'macd', 'signal', and 'histogram' arrays
        """
        if isinstance(prices, list):
            prices = np.array(prices, dtype=float)
        elif isinstance(prices, pd.Series):
            prices = prices.values

        # Calculate EMAs
        ema_fast = TechnicalIndicators.ema(prices, fast_period)
        ema_slow = TechnicalIndicators.ema(prices, slow_period)

        # MACD line
        macd_line = ema_fast - ema_slow

        # Signal line (EMA of MACD) - calculate on the full macd_line to preserve length
        signal_line = TechnicalIndicators.ema(macd_line, signal_period)

        # Histogram
        histogram = np.where(
            ~np.isnan(macd_line) & ~np.isnan(signal_line),
            macd_line - signal_line,
            np.nan
        )

        return {
            'macd': macd_line,
            'signal': signal_line,
            'histogram': histogram
        }

    @staticmethod
    def rsi(prices: Union[List[float], np.ndarray, pd.Series], period: int = 14) -> np.ndarray:
        """
        Relative Strength Index.

        Args:
            prices: Price series
            period: Number of periods (default: 14)

        Returns:
            Array of RSI values
        """
        if isinstance(prices, list):
            prices = np.array(prices, dtype=float)
        elif isinstance(prices, pd.Series):
            prices = prices.values

        if len(prices) < period + 1:
            return np.full(len(prices), np.nan)

        # Calculate price changes
        deltas = np.diff(prices)

        # Separate gains and losses
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        # Initialize RSI array
        rsi = np.full(len(prices), np.nan)

        # Calculate initial averages
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        # Calculate RSI
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

            if avg_loss == 0:
                rsi[i + 1] = 100
            else:
                rs = avg_gain / avg_loss
                rsi[i + 1] = 100 - (100 / (1 + rs))

        return rsi

    @staticmethod
    def kdj(high: Union[List[float], np.ndarray],
            low: Union[List[float], np.ndarray],
            close: Union[List[float], np.ndarray],
            period: int = 9,
            signal_period: int = 3) -> Dict[str, np.ndarray]:
        """
        KDJ Stochastic Oscillator.

        Args:
            high: High price series
            low: Low price series
            close: Close price series
            period: K period (default: 9)
            signal_period: D period (default: 3)

        Returns:
            Dictionary with 'k', 'd', and 'j' arrays
        """
        # Convert to numpy arrays
        high = np.array(high, dtype=float)
        low = np.array(low, dtype=float)
        close = np.array(close, dtype=float)

        n = len(close)
        k = np.full(n, np.nan)
        d = np.full(n, np.nan)
        j = np.full(n, np.nan)

        # Calculate K values
        for i in range(period - 1, n):
            highest = np.max(high[i - period + 1:i + 1])
            lowest = np.min(low[i - period + 1:i + 1])

            if highest != lowest:
                k[i] = (close[i] - lowest) / (highest - lowest) * 100
            else:
                k[i] = 50

        # Calculate D values (SMA of K)
        d = TechnicalIndicators.sma(k, signal_period)

        # Calculate J values
        j = 3 * k - 2 * d

        return {'k': k, 'd': d, 'j': j}

    @staticmethod
    def bollinger_bands(prices: Union[List[float], np.ndarray, pd.Series],
                        period: int = 20,
                        num_std: float = 2) -> Dict[str, np.ndarray]:
        """
        Bollinger Bands.

        Args:
            prices: Price series
            period: SMA period (default: 20)
            num_std: Number of standard deviations (default: 2)

        Returns:
            Dictionary with 'upper', 'middle', and 'lower' bands
        """
        if isinstance(prices, list):
            prices = np.array(prices, dtype=float)
        elif isinstance(prices, pd.Series):
            prices = prices.values

        # Calculate middle band (SMA)
        middle = TechnicalIndicators.sma(prices, period)

        # Calculate standard deviation
        std = np.full(len(prices), np.nan)
        for i in range(period - 1, len(prices)):
            std[i] = np.std(prices[i - period + 1:i + 1])

        # Calculate bands
        upper = middle + num_std * std
        lower = middle - num_std * std

        return {
            'upper': upper,
            'middle': middle,
            'lower': lower
        }

    @staticmethod
    def atr(high: Union[List[float], np.ndarray],
            low: Union[List[float], np.ndarray],
            close: Union[List[float], np.ndarray],
            period: int = 14) -> np.ndarray:
        """
        Average True Range.

        Args:
            high: High price series
            low: Low price series
            close: Close price series
            period: ATR period (default: 14)

        Returns:
            Array of ATR values
        """
        high = np.array(high, dtype=float)
        low = np.array(low, dtype=float)
        close = np.array(close, dtype=float)

        n = len(close)
        tr = np.zeros(n)

        # Calculate True Range
        for i in range(1, n):
            hl = high[i] - low[i]
            hc = abs(high[i] - close[i - 1])
            lc = abs(low[i] - close[i - 1])
            tr[i] = max(hl, hc, lc)

        # First TR is simply High - Low
        tr[0] = high[0] - low[0]

        # Calculate ATR using EMA
        atr = np.full(n, np.nan)
        atr[period - 1] = np.mean(tr[:period])

        for i in range(period, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

        return atr

    @staticmethod
    def obv(close: Union[List[float], np.ndarray],
            volume: Union[List[float], np.ndarray]) -> np.ndarray:
        """
        On-Balance Volume.

        Args:
            close: Close price series
            volume: Volume series

        Returns:
            Array of OBV values
        """
        close = np.array(close, dtype=float)
        volume = np.array(volume, dtype=float)

        obv = np.zeros(len(close))
        obv[0] = volume[0]

        for i in range(1, len(close)):
            if close[i] > close[i - 1]:
                obv[i] = obv[i - 1] + volume[i]
            elif close[i] < close[i - 1]:
                obv[i] = obv[i - 1] - volume[i]
            else:
                obv[i] = obv[i - 1]

        return obv

    @staticmethod
    def volume_ratio(volume: Union[List[float], np.ndarray],
                     period: int = 5) -> np.ndarray:
        """
        Volume Ratio.

        Args:
            volume: Volume series
            period: Period for average volume (default: 5)

        Returns:
            Array of volume ratio values
        """
        volume = np.array(volume, dtype=float)
        avg_volume = TechnicalIndicators.sma(volume, period)

        # Avoid division by zero
        volume_ratio = np.where(
            avg_volume != 0,
            volume / avg_volume,
            1.0
        )

        return volume_ratio

    @staticmethod
    def vwap(high: Union[List[float], np.ndarray],
             low: Union[List[float], np.ndarray],
             close: Union[List[float], np.ndarray],
             volume: Union[List[float], np.ndarray]) -> np.ndarray:
        """
        Volume Weighted Average Price.

        Args:
            high: High price series
            low: Low price series
            close: Close price series
            volume: Volume series

        Returns:
            Array of VWAP values
        """
        high = np.array(high, dtype=float)
        low = np.array(low, dtype=float)
        close = np.array(close, dtype=float)
        volume = np.array(volume, dtype=float)

        # Typical price
        typical_price = (high + low + close) / 3

        # Cumulative values
        cum_volume = np.cumsum(volume)
        cum_pv = np.cumsum(typical_price * volume)

        # VWAP
        vwap = np.where(cum_volume != 0, cum_pv / cum_volume, typical_price)

        return vwap

    @staticmethod
    def calculate_all_indicators(
        df: pd.DataFrame,
        price_col: str = 'close',
        high_col: str = 'high',
        low_col: str = 'low',
        volume_col: str = 'volume'
    ) -> pd.DataFrame:
        """
        Calculate all technical indicators for a DataFrame.

        Args:
            df: DataFrame with OHLCV data
            price_col: Column name for close price
            high_col: Column name for high price
            low_col: Column name for low price
            volume_col: Column name for volume

        Returns:
            DataFrame with all indicators added
        """
        result = df.copy()

        # Price-based indicators
        result['sma_5'] = TechnicalIndicators.sma(df[price_col], 5)
        result['sma_10'] = TechnicalIndicators.sma(df[price_col], 10)
        result['sma_20'] = TechnicalIndicators.sma(df[price_col], 20)
        result['sma_50'] = TechnicalIndicators.sma(df[price_col], 50)

        result['ema_5'] = TechnicalIndicators.ema(df[price_col], 5)
        result['ema_10'] = TechnicalIndicators.ema(df[price_col], 10)
        result['ema_20'] = TechnicalIndicators.ema(df[price_col], 20)

        # MACD
        macd = TechnicalIndicators.macd(df[price_col])
        result['macd'] = macd['macd']
        result['macd_signal'] = macd['signal']
        result['macd_histogram'] = macd['histogram']

        # RSI
        result['rsi_14'] = TechnicalIndicators.rsi(df[price_col], 14)

        # KDJ
        if high_col in df and low_col in df:
            kdj = TechnicalIndicators.kdj(df[high_col], df[low_col], df[price_col])
            result['kdj_k'] = kdj['k']
            result['kdj_d'] = kdj['d']
            result['kdj_j'] = kdj['j']

            # Bollinger Bands
            bb = TechnicalIndicators.bollinger_bands(df[price_col])
            result['bb_upper'] = bb['upper']
            result['bb_middle'] = bb['middle']
            result['bb_lower'] = bb['lower']

            # ATR
            result['atr_14'] = TechnicalIndicators.atr(df[high_col], df[low_col], df[price_col])

        # Volume indicators
        if volume_col in df:
            result['obv'] = TechnicalIndicators.obv(df[price_col], df[volume_col])
            result['volume_ratio'] = TechnicalIndicators.volume_ratio(df[volume_col])

            if high_col in df and low_col in df:
                result['vwap'] = TechnicalIndicators.vwap(
                    df[high_col], df[low_col], df[price_col], df[volume_col]
                )

        return result


# Convenience functions for common use cases
def calculate_batch_indicators(
    batch_requests: Union[
        IndicatorBatchRequest,
        Sequence[IndicatorBatchRequest],
        Mapping[str, Any],
    ],
    *,
    default_indicators: Optional[Sequence[str]] = None,
    price_col: str = 'close',
    high_col: str = 'high',
    low_col: str = 'low',
    volume_col: str = 'volume'
) -> Dict[str, pd.DataFrame]:
    """
    Calculate technical indicators for multiple symbols or datasets.

    Args:
        batch_requests: Iterable of indicator requests. Each entry can be:
            * `IndicatorBatchRequest`
            * Mapping with keys that match `IndicatorBatchRequest` fields
            * Tuple of `(data, indicators)` when using mapping-style input
        default_indicators: Indicators applied when a request omits explicit
            selections. Passing ``None`` calculates the full indicator suite.
        price_col: Default price column name (used when normalising mappings)
        high_col: Default high column name
        low_col: Default low column name
        volume_col: Default volume column name

    Returns:
        Dictionary mapping symbols to DataFrames with indicator columns.
    """

    normalized: List[IndicatorBatchRequest]
    normalized = []

    default_indicator_list = _normalize_indicator_list(default_indicators)

    def _clone_request(request: IndicatorBatchRequest) -> IndicatorBatchRequest:
        return IndicatorBatchRequest(
            symbol=request.symbol,
            data=request.data,
            indicators=_normalize_indicator_list(request.indicators),
            price_col=request.price_col,
            high_col=request.high_col,
            low_col=request.low_col,
            volume_col=request.volume_col,
            coerce_numeric=request.coerce_numeric,
            sort_by=request.sort_by,
        )

    def _append_request(symbol: str, **kwargs: Any) -> None:
        data = kwargs.get('data')
        if data is None:
            logger.error(f"Missing data for symbol '{symbol}' in batch indicator request")
            return

        indicators = kwargs.get('indicators', None)
        indicators = _normalize_indicator_list(indicators)
        if indicators is None:
            indicators = default_indicator_list

        normalized.append(
            IndicatorBatchRequest(
                symbol=symbol,
                data=data,
                indicators=indicators,
                price_col=kwargs.get('price_col', price_col),
                high_col=kwargs.get('high_col', high_col),
                low_col=kwargs.get('low_col', low_col),
                volume_col=kwargs.get('volume_col', volume_col),
                coerce_numeric=kwargs.get('coerce_numeric', True),
                sort_by=kwargs.get('sort_by', 'timestamp'),
            )
        )

    # Normalise different input forms
    if isinstance(batch_requests, IndicatorBatchRequest):
        normalized.append(_clone_request(batch_requests))
    elif isinstance(batch_requests, Mapping):
        for symbol, payload in batch_requests.items():
            if isinstance(payload, Mapping):
                data = payload.get('data')
                if data is None:
                    data = payload.get('klines')
                if data is None:
                    data = payload.get('records')

                _append_request(
                    symbol,
                    data=data,
                    indicators=payload.get('indicators'),
                    price_col=payload.get('price_col', price_col),
                    high_col=payload.get('high_col', high_col),
                    low_col=payload.get('low_col', low_col),
                    volume_col=payload.get('volume_col', volume_col),
                    coerce_numeric=payload.get('coerce_numeric', True),
                    sort_by=payload.get('sort_by', 'timestamp'),
                )
            elif isinstance(payload, tuple):
                if not payload:
                    logger.error(f"Empty tuple payload for symbol '{symbol}'")
                    continue

                data = payload[0]
                indicators = payload[1] if len(payload) > 1 else None
                _append_request(symbol, data=data, indicators=indicators)
            else:
                _append_request(symbol, data=payload)
    elif isinstance(batch_requests, SequenceCollection) and not isinstance(batch_requests, (str, bytes)):
        for item in batch_requests:
            if isinstance(item, IndicatorBatchRequest):
                normalized.append(_clone_request(item))
            elif isinstance(item, Mapping):
                symbol = item.get('symbol')
                data = item.get('data')
                if data is None:
                    data = item.get('klines')
                if data is None:
                    data = item.get('records')
                if symbol is None:
                    logger.error("Batch indicator mapping entry missing 'symbol'")
                    continue
                _append_request(
                    symbol,
                    data=data,
                    indicators=item.get('indicators'),
                    price_col=item.get('price_col', price_col),
                    high_col=item.get('high_col', high_col),
                    low_col=item.get('low_col', low_col),
                    volume_col=item.get('volume_col', volume_col),
                    coerce_numeric=item.get('coerce_numeric', True),
                    sort_by=item.get('sort_by', 'timestamp'),
                )
            elif isinstance(item, tuple):
                if not item:
                    logger.error("Encountered empty tuple in batch indicator requests")
                    continue

                symbol = item[0]
                if not isinstance(symbol, str):
                    logger.error(
                        "Tuple-based batch indicator request must start with symbol string"
                    )
                    continue

                data = item[1] if len(item) > 1 else None
                indicators = item[2] if len(item) > 2 else None
                _append_request(symbol, data=data, indicators=indicators)
            else:
                logger.error(f"Unsupported batch indicator entry type: {type(item)!r}")
    else:
        raise TypeError(
            "batch_requests must be an IndicatorBatchRequest, a sequence of requests, or a mapping"
        )

    results: Dict[str, pd.DataFrame] = {}

    for request in normalized:
        try:
            df = _ensure_dataframe(request.data)
        except TypeError as exc:
            logger.error(f"{request.symbol}: {exc}")
            results[request.symbol] = pd.DataFrame()
            continue

        if request.sort_by and request.sort_by in df.columns:
            df = df.sort_values(request.sort_by)

        df = df.reset_index(drop=True)

        if request.coerce_numeric:
            df = _coerce_numeric_columns(
                df,
                [request.price_col, request.high_col, request.low_col, request.volume_col],
            )

        if request.price_col not in df.columns:
            logger.error(
                f"{request.symbol}: missing price column '{request.price_col}' for indicator calculation"
            )
            results[request.symbol] = df
            continue

        indicator_df = TechnicalIndicators.calculate_all_indicators(
            df,
            price_col=request.price_col,
            high_col=request.high_col,
            low_col=request.low_col,
            volume_col=request.volume_col,
        )

        filtered = _filter_indicator_columns(indicator_df, request)
        results[request.symbol] = filtered

    return results


def calculate_indicators(klines: List[Dict], indicators: List[str] = None) -> pd.DataFrame:
    """
    Calculate indicators from K-line data.

    Args:
        klines: List of K-line dictionaries
        indicators: List of indicator names to calculate (None = all)

    Returns:
        DataFrame with indicators
    """
    request = IndicatorBatchRequest(
        symbol='__single__',
        data=klines,
        indicators=_normalize_indicator_list(indicators),
    )

    result = calculate_batch_indicators([request])
    return result.get('__single__', pd.DataFrame())
