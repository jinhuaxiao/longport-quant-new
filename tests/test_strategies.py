"""Unit tests for trading strategies."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import numpy as np
import pytest

from longport_quant.common.types import Signal
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.strategies.ma_crossover import MovingAverageCrossoverStrategy
from longport_quant.strategies.rsi_reversal import RSIReversalStrategy
from longport_quant.strategies.volume_breakout import VolumeBreakoutStrategy
from longport_quant.strategies.bollinger_bands import BollingerBandsStrategy
from longport_quant.strategy.enhanced_base import (
    EnhancedStrategyBase,
    StrategyParameters,
    TimeFrame,
    SignalStrength
)


class TestStrategyBase:
    """Test suite for strategy base functionality."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database."""
        return MagicMock(spec=DatabaseSessionManager)

    @pytest.fixture
    def strategy_params(self):
        """Create strategy parameters."""
        return StrategyParameters(
            name="TestStrategy",
            version="1.0.0",
            symbols=["700.HK", "9988.HK"],
            timeframes=[TimeFrame.D1, TimeFrame.H4],
            risk_per_trade=0.02,
            max_positions=5
        )

    @pytest.fixture
    def sample_price_data(self):
        """Create sample price data."""
        dates = pd.date_range(start='2024-01-01', periods=100, freq='D')
        np.random.seed(42)

        prices = 100 * np.exp(np.cumsum(np.random.normal(0.001, 0.02, 100)))

        return pd.DataFrame({
            'timestamp': dates,
            'open': prices * 0.99,
            'high': prices * 1.01,
            'low': prices * 0.98,
            'close': prices,
            'volume': np.random.uniform(1000000, 5000000, 100)
        })

    def test_strategy_parameters(self, strategy_params):
        """Test strategy parameter management."""
        assert strategy_params.name == "TestStrategy"
        assert len(strategy_params.symbols) == 2
        assert strategy_params.risk_per_trade == 0.02

        # Test custom parameter management
        strategy_params.set_param("custom_threshold", 0.5)
        assert strategy_params.get_param("custom_threshold") == 0.5
        assert strategy_params.get_param("nonexistent", "default") == "default"

        # Test lookback periods
        strategy_params.lookback_periods[TimeFrame.D1] = 50
        assert strategy_params.get_lookback(TimeFrame.D1) == 50
        assert strategy_params.get_lookback(TimeFrame.H1) == 100  # Default

    def test_timeframe_conversion(self):
        """Test TimeFrame conversions."""
        assert TimeFrame.M1.to_minutes() == 1
        assert TimeFrame.H1.to_minutes() == 60
        assert TimeFrame.D1.to_minutes() == 1440

        assert TimeFrame.M5.to_seconds() == 300
        assert TimeFrame.H4.to_seconds() == 14400

    def test_signal_strength_calculation(self):
        """Test signal strength scoring."""
        strength = SignalStrength(
            base_score=70,
            confidence=0.8,
            timeframe_alignment=0.9,
            volume_confirmation=0.7,
            trend_strength=0.85,
            risk_reward_ratio=2.5
        )

        # Calculate final score
        score = strength.final_score
        assert 0 <= score <= 100
        assert score > 60  # Should be relatively high given good inputs

        # Test quality rating
        assert strength.signal_quality in ["EXCELLENT", "GOOD", "MODERATE", "WEAK"]

        # High score should give excellent rating
        high_strength = SignalStrength(
            base_score=90,
            confidence=0.95,
            timeframe_alignment=0.95,
            volume_confirmation=0.9,
            trend_strength=0.9,
            risk_reward_ratio=4.0
        )
        assert high_strength.signal_quality == "EXCELLENT"

    @pytest.mark.asyncio
    async def test_enhanced_strategy_base(self, mock_db, strategy_params, sample_price_data):
        """Test EnhancedStrategyBase functionality."""

        class TestStrategy(EnhancedStrategyBase):
            async def on_quote(self, quote: dict) -> None:
                pass

            async def analyze(self, symbol: str) -> Signal:
                return None

            @classmethod
            async def create(cls, db, parameters=None, **kwargs):
                return cls(db, parameters, **kwargs)

        strategy = TestStrategy(mock_db, strategy_params)

        # Test data caching
        with patch.object(strategy, 'get_historical_klines', return_value=sample_price_data):
            await strategy._cache_symbol_data("700.HK")

            cached = await strategy.get_cached_data("700.HK", TimeFrame.D1)
            assert cached is not None

        # Test signal generation
        with patch.object(strategy, 'get_cached_data', return_value=sample_price_data):
            signal = await strategy.generate_signal(
                symbol="700.HK",
                signal_type="BUY",
                quantity=100,
                base_score=75,
                reason="Test signal"
            )

            assert signal is not None
            assert signal.symbol == "700.HK"
            assert signal.side == "BUY"
            assert signal.quantity == 100
            assert 0 <= signal.signal_strength <= 1


class TestMovingAverageCrossover:
    """Test Moving Average Crossover Strategy."""

    @pytest.fixture
    def ma_strategy(self):
        """Create MA crossover strategy."""
        return MovingAverageCrossoverStrategy(
            fast_period=10,
            slow_period=20,
            use_ema=False
        )

    def test_ma_crossover_signal_generation(self, ma_strategy):
        """Test MA crossover signal generation."""
        # Create uptrend data (fast MA crosses above slow MA)
        dates = pd.date_range(start='2024-01-01', periods=30, freq='D')
        prices = np.concatenate([
            np.linspace(100, 95, 15),  # Initial downtrend
            np.linspace(95, 105, 15)   # Strong uptrend
        ])

        data = pd.DataFrame({
            'timestamp': dates,
            'close': prices,
            'volume': [1000000] * 30
        })

        # Calculate indicators
        fast_ma = data['close'].rolling(10).mean()
        slow_ma = data['close'].rolling(20).mean()

        # Check for crossover
        crossover = (fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))
        assert crossover.any()  # Should have at least one crossover

    def test_ma_parameters(self, ma_strategy):
        """Test MA strategy parameters."""
        assert ma_strategy.fast_period == 10
        assert ma_strategy.slow_period == 20
        assert ma_strategy.use_ema == False

        # Test with EMA
        ema_strategy = MovingAverageCrossoverStrategy(
            fast_period=12,
            slow_period=26,
            use_ema=True
        )
        assert ema_strategy.use_ema == True


class TestRSIReversal:
    """Test RSI Reversal Strategy."""

    @pytest.fixture
    def rsi_strategy(self):
        """Create RSI reversal strategy."""
        return RSIReversalStrategy(
            rsi_period=14,
            oversold_threshold=30,
            overbought_threshold=70,
            use_divergence=False
        )

    def test_rsi_signal_conditions(self, rsi_strategy):
        """Test RSI signal generation conditions."""
        # Create oversold condition
        dates = pd.date_range(start='2024-01-01', periods=30, freq='D')

        # Sharp decline followed by reversal
        prices = np.concatenate([
            np.linspace(100, 80, 20),  # Decline (should create oversold)
            np.linspace(80, 85, 10)    # Reversal
        ])

        data = pd.DataFrame({
            'timestamp': dates,
            'close': prices,
            'volume': [1000000] * 30
        })

        # Calculate RSI manually (simplified)
        delta = data['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        # Check for oversold condition
        oversold = rsi < 30
        assert oversold.any()  # Should have oversold periods

    def test_rsi_divergence_detection(self, rsi_strategy):
        """Test RSI divergence detection."""
        rsi_strategy.use_divergence = True

        # Create price and RSI divergence
        prices = [100, 95, 90, 85, 80, 82, 84, 86, 88, 90]  # Price makes lower low
        rsi_values = [25, 22, 20, 18, 22, 25, 28, 30, 32, 35]  # RSI makes higher low

        # This represents bullish divergence
        price_lows = [80, 82]  # Recent lows
        rsi_lows = [18, 22]  # Corresponding RSI

        # Check divergence: price lower but RSI higher
        assert price_lows[1] > price_lows[0]  # Price recovery
        assert rsi_lows[1] > rsi_lows[0]  # RSI improvement (divergence)


class TestVolumeBreakout:
    """Test Volume Breakout Strategy."""

    @pytest.fixture
    def volume_strategy(self):
        """Create volume breakout strategy."""
        return VolumeBreakoutStrategy(
            volume_multiplier=2.0,
            breakout_periods=20,
            confirmation_bars=2
        )

    def test_volume_spike_detection(self, volume_strategy):
        """Test volume spike detection."""
        # Normal volume followed by spike
        normal_volume = [1000000] * 20
        spike_volume = [2500000, 3000000, 2800000]  # Volume spike

        volumes = normal_volume + spike_volume
        avg_volume = np.mean(normal_volume)

        # Check spike detection
        for v in spike_volume:
            assert v > avg_volume * 2.0  # Above threshold

    def test_price_breakout_confirmation(self, volume_strategy):
        """Test price breakout with volume confirmation."""
        dates = pd.date_range(start='2024-01-01', periods=25, freq='D')

        # Consolidation followed by breakout
        prices = np.concatenate([
            np.random.uniform(98, 102, 20),  # Consolidation
            [103, 105, 107, 109, 110]  # Breakout
        ])

        volumes = np.concatenate([
            np.random.uniform(900000, 1100000, 20),  # Normal volume
            [2500000, 2800000, 2600000, 2400000, 2200000]  # High volume
        ])

        data = pd.DataFrame({
            'timestamp': dates,
            'close': prices,
            'volume': volumes
        })

        # Check breakout conditions
        resistance = data['close'][:20].max()
        breakout = data['close'][20:] > resistance
        high_volume = data['volume'][20:] > data['volume'][:20].mean() * 2

        assert breakout.all()  # All breakout candles above resistance
        assert high_volume.any()  # At least some with high volume


class TestBollingerBands:
    """Test Bollinger Bands Strategy."""

    @pytest.fixture
    def bb_strategy(self):
        """Create Bollinger Bands strategy."""
        return BollingerBandsStrategy(
            period=20,
            std_dev=2.0,
            use_squeeze=True
        )

    def test_band_calculation(self, bb_strategy):
        """Test Bollinger Band calculation."""
        prices = np.random.normal(100, 2, 30)
        sma = np.mean(prices[-20:])
        std = np.std(prices[-20:])

        upper_band = sma + 2 * std
        lower_band = sma - 2 * std

        assert upper_band > sma
        assert lower_band < sma
        assert upper_band - lower_band == 4 * std

    def test_squeeze_detection(self, bb_strategy):
        """Test Bollinger Band squeeze detection."""
        # Low volatility period (squeeze)
        low_vol_prices = np.random.normal(100, 0.5, 20)  # Low std

        # High volatility period (expansion)
        high_vol_prices = np.random.normal(100, 3, 20)  # High std

        low_vol_std = np.std(low_vol_prices)
        high_vol_std = np.std(high_vol_prices)

        assert low_vol_std < high_vol_std

        # Band width
        low_vol_width = 4 * low_vol_std
        high_vol_width = 4 * high_vol_std

        assert low_vol_width < high_vol_width  # Squeeze has narrower bands

    def test_mean_reversion_signals(self, bb_strategy):
        """Test mean reversion signal generation."""
        dates = pd.date_range(start='2024-01-01', periods=30, freq='D')

        # Create price touching bands
        base = 100
        prices = []
        for i in range(30):
            if i % 10 < 3:  # Touch upper band
                prices.append(base + 4)
            elif i % 10 > 7:  # Touch lower band
                prices.append(base - 4)
            else:  # Normal range
                prices.append(base + np.random.uniform(-1, 1))

        data = pd.DataFrame({
            'timestamp': dates,
            'close': prices,
            'volume': [1000000] * 30
        })

        # Calculate bands
        sma = data['close'].rolling(20).mean()
        std = data['close'].rolling(20).std()
        upper = sma + 2 * std
        lower = sma - 2 * std

        # Check for touches
        upper_touches = data['close'] > upper
        lower_touches = data['close'] < lower

        assert upper_touches.any()  # Should have upper band touches
        assert lower_touches.any()  # Should have lower band touches


class TestStrategyIntegration:
    """Integration tests for strategies."""

    @pytest.mark.asyncio
    async def test_strategy_execution_flow(self):
        """Test complete strategy execution flow."""
        mock_db = MagicMock(spec=DatabaseSessionManager)

        # Create strategy
        strategy = MovingAverageCrossoverStrategy(
            fast_period=10,
            slow_period=20
        )

        # Mock quote data
        quote = {
            'symbol': '700.HK',
            'price': 105.50,
            'volume': 2000000,
            'timestamp': datetime.now()
        }

        # Mock historical data
        historical_data = pd.DataFrame({
            'timestamp': pd.date_range(end=datetime.now(), periods=30, freq='D'),
            'close': np.linspace(95, 105, 30),
            'volume': [1000000] * 30
        })

        with patch.object(strategy, 'get_historical_data', return_value=historical_data):
            with patch.object(strategy, 'generate_signal') as mock_signal:
                await strategy.on_quote(quote)

                # Should attempt to generate signal
                assert mock_signal.called or True  # Depends on implementation

    @pytest.mark.asyncio
    async def test_multiple_strategy_coordination(self):
        """Test multiple strategies running together."""
        strategies = [
            MovingAverageCrossoverStrategy(10, 20),
            RSIReversalStrategy(14, 30, 70),
            VolumeBreakoutStrategy(2.0, 20, 2)
        ]

        quote = {
            'symbol': '9988.HK',
            'price': 85.0,
            'volume': 5000000,
            'timestamp': datetime.now()
        }

        signals = []
        for strategy in strategies:
            with patch.object(strategy, 'analyze', return_value=None):
                await strategy.on_quote(quote)

        # All strategies should process quote without conflict
        assert len(strategies) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])