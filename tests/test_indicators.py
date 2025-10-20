"""Unit tests for technical indicators."""

import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timedelta

from longport_quant.features.technical_indicators import TechnicalIndicators


class TestTechnicalIndicators:
    """Test suite for technical indicators."""

    @pytest.fixture
    def sample_price_data(self):
        """Create sample price data for testing."""
        dates = pd.date_range(start='2024-01-01', periods=100, freq='D')
        np.random.seed(42)

        # Generate realistic price data
        base_price = 100
        returns = np.random.normal(0.001, 0.02, 100)
        prices = base_price * np.exp(np.cumsum(returns))

        df = pd.DataFrame({
            'timestamp': dates,
            'open': prices * (1 + np.random.uniform(-0.01, 0.01, 100)),
            'high': prices * (1 + np.random.uniform(0, 0.02, 100)),
            'low': prices * (1 - np.random.uniform(0, 0.02, 100)),
            'close': prices,
            'volume': np.random.uniform(1000000, 5000000, 100).astype(int)
        })

        # Ensure high/low are correct
        df['high'] = df[['open', 'high', 'close']].max(axis=1)
        df['low'] = df[['open', 'low', 'close']].min(axis=1)

        return df

    @pytest.fixture
    def indicators(self):
        """Create TechnicalIndicators instance."""
        return TechnicalIndicators()

    def test_sma_calculation(self, indicators, sample_price_data):
        """Test Simple Moving Average calculation."""
        period = 20
        result = indicators.calculate_ma(sample_price_data, period)

        # Check output structure
        assert 'SMA_20' in result.columns
        assert len(result) == len(sample_price_data)

        # Check calculation correctness (manual verification for last value)
        expected_sma = sample_price_data['close'].tail(period).mean()
        calculated_sma = result['SMA_20'].iloc[-1]
        assert abs(expected_sma - calculated_sma) < 0.01

        # Check NaN handling
        assert result['SMA_20'].iloc[:period-1].isna().all()
        assert not result['SMA_20'].iloc[period:].isna().any()

    def test_ema_calculation(self, indicators, sample_price_data):
        """Test Exponential Moving Average calculation."""
        period = 20
        result = indicators.calculate_ema(sample_price_data, period)

        assert 'EMA_20' in result.columns
        assert len(result) == len(sample_price_data)

        # EMA should respond faster to recent prices than SMA
        sma_result = indicators.calculate_ma(sample_price_data, period)
        last_price = sample_price_data['close'].iloc[-1]

        if last_price > sample_price_data['close'].iloc[-period:].mean():
            # If recent price is above average, EMA should be higher than SMA
            assert result['EMA_20'].iloc[-1] > sma_result['SMA_20'].iloc[-1]

    def test_macd_calculation(self, indicators, sample_price_data):
        """Test MACD calculation."""
        result = indicators.calculate_macd(
            sample_price_data,
            fast_period=12,
            slow_period=26,
            signal_period=9
        )

        # Check all MACD components
        assert 'MACD' in result.columns
        assert 'MACD_signal' in result.columns
        assert 'MACD_histogram' in result.columns

        # Verify MACD line calculation
        ema_12 = indicators.calculate_ema(sample_price_data, 12)['EMA_12']
        ema_26 = indicators.calculate_ema(sample_price_data, 26)['EMA_26']
        expected_macd = ema_12 - ema_26

        # Compare last few values (allowing for floating point errors)
        np.testing.assert_allclose(
            result['MACD'].iloc[-5:],
            expected_macd.iloc[-5:],
            rtol=1e-5
        )

        # Histogram should be MACD - Signal
        expected_hist = result['MACD'] - result['MACD_signal']
        np.testing.assert_allclose(
            result['MACD_histogram'].dropna(),
            expected_hist.dropna(),
            rtol=1e-5
        )

    def test_rsi_calculation(self, indicators, sample_price_data):
        """Test RSI calculation."""
        period = 14
        result = indicators.calculate_rsi(sample_price_data, period)

        assert 'RSI_14' in result.columns

        # RSI should be between 0 and 100
        rsi_values = result['RSI_14'].dropna()
        assert (rsi_values >= 0).all()
        assert (rsi_values <= 100).all()

        # Test extreme cases
        # Create trending data
        trending_up = sample_price_data.copy()
        trending_up['close'] = range(100, 200)
        rsi_up = indicators.calculate_rsi(trending_up, period)
        assert rsi_up['RSI_14'].iloc[-1] > 70  # Should show overbought

        trending_down = sample_price_data.copy()
        trending_down['close'] = range(200, 100, -1)
        rsi_down = indicators.calculate_rsi(trending_down, period)
        assert rsi_down['RSI_14'].iloc[-1] < 30  # Should show oversold

    def test_kdj_calculation(self, indicators, sample_price_data):
        """Test KDJ calculation."""
        result = indicators.calculate_kdj(sample_price_data)

        # Check all KDJ components
        assert 'K' in result.columns
        assert 'D' in result.columns
        assert 'J' in result.columns

        # K should be between 0 and 100 (mostly)
        k_values = result['K'].dropna()
        assert (k_values >= 0).sum() > len(k_values) * 0.95
        assert (k_values <= 100).sum() > len(k_values) * 0.95

        # D should be smoother than K (lower std)
        assert result['D'].std() < result['K'].std()

        # J = 3*K - 2*D
        expected_j = 3 * result['K'] - 2 * result['D']
        np.testing.assert_allclose(
            result['J'].dropna(),
            expected_j.dropna(),
            rtol=1e-5
        )

    def test_bollinger_bands(self, indicators, sample_price_data):
        """Test Bollinger Bands calculation."""
        result = indicators.calculate_bollinger_bands(sample_price_data, period=20, std_dev=2)

        assert 'BB_upper' in result.columns
        assert 'BB_middle' in result.columns
        assert 'BB_lower' in result.columns
        assert 'BB_width' in result.columns
        assert 'BB_percent' in result.columns

        # Middle band should be SMA
        sma = indicators.calculate_ma(sample_price_data, 20)['SMA_20']
        np.testing.assert_allclose(
            result['BB_middle'].dropna(),
            sma.dropna(),
            rtol=1e-5
        )

        # Upper band should be above middle, lower below
        assert (result['BB_upper'] > result['BB_middle']).all()
        assert (result['BB_lower'] < result['BB_middle']).all()

        # Width should be positive
        assert (result['BB_width'] > 0).all()

        # Percent should mostly be between 0 and 1
        percent_in_range = ((result['BB_percent'] >= 0) & (result['BB_percent'] <= 1)).sum()
        assert percent_in_range > len(result) * 0.8

    def test_atr_calculation(self, indicators, sample_price_data):
        """Test Average True Range calculation."""
        period = 14
        result = indicators.calculate_atr(sample_price_data, period)

        assert 'ATR_14' in result.columns

        # ATR should be positive
        atr_values = result['ATR_14'].dropna()
        assert (atr_values > 0).all()

        # ATR should be reasonable relative to price
        avg_price = sample_price_data['close'].mean()
        avg_atr = atr_values.mean()
        atr_percent = avg_atr / avg_price
        assert 0.001 < atr_percent < 0.1  # Between 0.1% and 10%

    def test_volume_indicators(self, indicators, sample_price_data):
        """Test volume-based indicators."""
        # OBV
        result_obv = indicators.calculate_obv(sample_price_data)
        assert 'OBV' in result_obv.columns

        # Check OBV logic
        price_changes = sample_price_data['close'].diff()
        for i in range(1, len(result_obv)):
            if price_changes.iloc[i] > 0:
                # Volume should be added
                expected_change = sample_price_data['volume'].iloc[i]
            elif price_changes.iloc[i] < 0:
                # Volume should be subtracted
                expected_change = -sample_price_data['volume'].iloc[i]
            else:
                expected_change = 0

            if i > 1:  # Skip first few due to initialization
                actual_change = result_obv['OBV'].iloc[i] - result_obv['OBV'].iloc[i-1]
                assert abs(actual_change - expected_change) < 1

        # Volume Ratio
        result_vr = indicators.calculate_volume_ratio(sample_price_data)
        assert 'volume_ratio' in result_vr.columns
        assert (result_vr['volume_ratio'] > 0).all()

    def test_all_indicators(self, indicators, sample_price_data):
        """Test calculating all indicators at once."""
        result = indicators.calculate_all(sample_price_data)

        # Check that all expected indicators are present
        expected_columns = [
            'SMA_10', 'SMA_20', 'SMA_50',
            'EMA_12', 'EMA_26',
            'MACD', 'MACD_signal', 'MACD_histogram',
            'RSI_14',
            'K', 'D', 'J',
            'BB_upper', 'BB_middle', 'BB_lower',
            'ATR_14',
            'OBV',
            'volume_ratio'
        ]

        for col in expected_columns:
            assert col in result.columns, f"Missing indicator: {col}"

        # Result should have same length as input
        assert len(result) == len(sample_price_data)

    def test_indicator_with_insufficient_data(self, indicators):
        """Test indicators with insufficient data."""
        # Create very small dataset
        small_data = pd.DataFrame({
            'timestamp': pd.date_range(start='2024-01-01', periods=5),
            'close': [100, 101, 99, 102, 100],
            'high': [101, 102, 100, 103, 101],
            'low': [99, 100, 98, 101, 99],
            'volume': [1000, 1100, 900, 1200, 1000]
        })

        # Should handle gracefully with NaNs
        result = indicators.calculate_ma(small_data, period=10)
        assert 'SMA_10' in result.columns
        assert result['SMA_10'].isna().all()  # All NaN due to insufficient data

    def test_indicator_with_missing_values(self, indicators):
        """Test indicators with missing values in data."""
        data_with_gaps = pd.DataFrame({
            'timestamp': pd.date_range(start='2024-01-01', periods=30),
            'close': [100 + i if i % 5 != 0 else np.nan for i in range(30)],
            'high': [102 + i if i % 5 != 0 else np.nan for i in range(30)],
            'low': [98 + i if i % 5 != 0 else np.nan for i in range(30)],
            'volume': [1000 * (i + 1) for i in range(30)]
        })

        # Should handle NaNs appropriately
        result = indicators.calculate_ma(data_with_gaps, period=5)
        assert 'SMA_5' in result.columns
        # Some values should be calculated despite gaps
        assert not result['SMA_5'].isna().all()

    def test_custom_parameters(self, indicators, sample_price_data):
        """Test indicators with custom parameters."""
        # Custom MA period
        ma_result = indicators.calculate_ma(sample_price_data, period=15)
        assert 'SMA_15' in ma_result.columns

        # Custom RSI period
        rsi_result = indicators.calculate_rsi(sample_price_data, period=21)
        assert 'RSI_21' in rsi_result.columns

        # Custom Bollinger Bands
        bb_result = indicators.calculate_bollinger_bands(
            sample_price_data,
            period=25,
            std_dev=2.5
        )
        assert 'BB_upper' in bb_result.columns

        # Verify bands are wider with larger std_dev
        bb_narrow = indicators.calculate_bollinger_bands(
            sample_price_data,
            period=25,
            std_dev=1.5
        )
        assert (bb_result['BB_width'] > bb_narrow['BB_width']).mean() > 0.9


@pytest.fixture(scope="module")
def sample_intraday_data():
    """Create sample intraday data for testing."""
    times = pd.date_range(start='2024-01-01 09:30', periods=390, freq='1min')
    np.random.seed(123)

    prices = 100 * np.exp(np.cumsum(np.random.normal(0, 0.0001, 390)))

    return pd.DataFrame({
        'timestamp': times,
        'open': prices * (1 + np.random.uniform(-0.001, 0.001, 390)),
        'high': prices * (1 + np.random.uniform(0, 0.002, 390)),
        'low': prices * (1 - np.random.uniform(0, 0.002, 390)),
        'close': prices,
        'volume': np.random.uniform(10000, 50000, 390).astype(int)
    })


if __name__ == "__main__":
    pytest.main([__file__, "-v"])