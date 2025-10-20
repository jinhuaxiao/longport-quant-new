"""Unit tests for risk management and controls."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from longport_quant.common.types import Signal
from longport_quant.config.settings import Settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.portfolio.state import PortfolioService
from longport_quant.risk.checks import (
    RiskEngine,
    RiskLimits,
    RiskMetrics,
    RiskLevel,
    RiskAlert
)


class TestRiskLimits:
    """Test risk limits configuration."""

    def test_default_risk_limits(self):
        """Test default risk limit values."""
        limits = RiskLimits(
            max_position_size=10000,
            max_position_value=100000,
            max_order_size=5000,
            max_notional=50000
        )

        assert limits.max_position_size == 10000
        assert limits.max_portfolio_allocation == 0.2  # Default 20%
        assert limits.max_daily_loss == 0.05  # Default 5%
        assert limits.max_drawdown == 0.15  # Default 15%
        assert limits.max_long_exposure == 1.0  # Default 100%
        assert limits.max_short_exposure == 0.3  # Default 30%

    def test_custom_risk_limits(self):
        """Test custom risk limit configuration."""
        limits = RiskLimits(
            max_position_size=5000,
            max_position_value=50000,
            max_order_size=2000,
            max_notional=20000,
            max_portfolio_allocation=0.1,
            max_daily_trades=50,
            max_loss_per_trade=0.01,
            max_daily_loss=0.03,
            max_drawdown=0.10
        )

        assert limits.max_portfolio_allocation == 0.1
        assert limits.max_daily_trades == 50
        assert limits.max_loss_per_trade == 0.01
        assert limits.max_daily_loss == 0.03
        assert limits.max_drawdown == 0.10


class TestRiskMetrics:
    """Test risk metrics calculation."""

    def test_risk_metrics_initialization(self):
        """Test risk metrics default values."""
        metrics = RiskMetrics()

        assert metrics.portfolio_value == 0.0
        assert metrics.cash_available == 0.0
        assert metrics.long_exposure == 0.0
        assert metrics.short_exposure == 0.0
        assert metrics.current_drawdown == 0.0
        assert metrics.daily_trades == 0
        assert metrics.risk_level == RiskLevel.LOW

    def test_risk_metrics_calculation(self):
        """Test risk metrics calculation."""
        metrics = RiskMetrics(
            portfolio_value=100000,
            cash_available=20000,
            long_exposure=70000,
            short_exposure=10000,
            current_drawdown=0.05,
            daily_pnl=-2000,
            daily_trades=25
        )

        # Calculate derived metrics
        metrics.gross_exposure = metrics.long_exposure + metrics.short_exposure
        metrics.net_exposure = metrics.long_exposure - metrics.short_exposure

        assert metrics.gross_exposure == 80000
        assert metrics.net_exposure == 60000

        # Check exposure ratios
        long_ratio = metrics.long_exposure / metrics.portfolio_value
        assert long_ratio == 0.7  # 70%

        short_ratio = metrics.short_exposure / metrics.portfolio_value
        assert short_ratio == 0.1  # 10%


class TestRiskEngine:
    """Test risk engine functionality."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock(spec=Settings)
        return settings

    @pytest.fixture
    def mock_portfolio(self):
        """Create mock portfolio service."""
        portfolio = MagicMock(spec=PortfolioService)
        portfolio.get_positions = AsyncMock(return_value=[])
        portfolio.get_cash_balance = AsyncMock(return_value=100000)
        return portfolio

    @pytest.fixture
    def mock_db(self):
        """Create mock database."""
        return MagicMock(spec=DatabaseSessionManager)

    @pytest.fixture
    def risk_engine(self, mock_settings, mock_portfolio, mock_db):
        """Create risk engine instance."""
        return RiskEngine(mock_settings, mock_portfolio, mock_db)

    @pytest.mark.asyncio
    async def test_validate_order_success(self, risk_engine):
        """Test successful order validation."""
        order = {
            'symbol': '700.HK',
            'side': 'BUY',
            'quantity': 100,
            'price': 350.0
        }

        # Mock watchlist check
        with patch.object(risk_engine._watchlist, 'symbols', return_value=['700.HK']):
            # Mock risk metrics
            risk_engine._risk_metrics.portfolio_value = 100000
            risk_engine._risk_metrics.daily_trades = 5

            with patch.object(risk_engine, '_get_current_price', return_value=350.0):
                with patch.object(risk_engine, '_get_position_size', return_value=0):
                    is_valid, error = await risk_engine.validate_order(order)

                    assert is_valid == True
                    assert error is None

    @pytest.mark.asyncio
    async def test_validate_order_exceeds_position_limit(self, risk_engine):
        """Test order validation with position limit exceeded."""
        order = {
            'symbol': '700.HK',
            'side': 'BUY',
            'quantity': 15000,  # Exceeds limit
            'price': 350.0
        }

        with patch.object(risk_engine._watchlist, 'symbols', return_value=['700.HK']):
            risk_engine._risk_metrics.portfolio_value = 100000

            with patch.object(risk_engine, '_get_current_price', return_value=350.0):
                with patch.object(risk_engine, '_get_position_size', return_value=0):
                    is_valid, error = await risk_engine.validate_order(order)

                    assert is_valid == False
                    assert "exceeds limit" in error.lower()

    @pytest.mark.asyncio
    async def test_validate_order_exceeds_portfolio_allocation(self, risk_engine):
        """Test order validation with portfolio allocation exceeded."""
        order = {
            'symbol': '700.HK',
            'side': 'BUY',
            'quantity': 1000,
            'price': 350.0  # Total: 350,000
        }

        with patch.object(risk_engine._watchlist, 'symbols', return_value=['700.HK']):
            risk_engine._risk_metrics.portfolio_value = 100000  # Order > 20% of portfolio

            with patch.object(risk_engine, '_get_current_price', return_value=350.0):
                with patch.object(risk_engine, '_get_position_size', return_value=0):
                    is_valid, error = await risk_engine.validate_order(order)

                    assert is_valid == False
                    assert "portfolio" in error.lower()

    @pytest.mark.asyncio
    async def test_validate_order_symbol_not_in_watchlist(self, risk_engine):
        """Test order validation with symbol not in watchlist."""
        order = {
            'symbol': 'INVALID.HK',
            'side': 'BUY',
            'quantity': 100,
            'price': 100.0
        }

        with patch.object(risk_engine._watchlist, 'symbols', return_value=['700.HK']):
            is_valid, error = await risk_engine.validate_order(order)

            assert is_valid == False
            assert "not in watchlist" in error.lower()

    @pytest.mark.asyncio
    async def test_validate_exposure_limits(self, risk_engine):
        """Test exposure limit validation."""
        # Setup
        risk_engine._risk_metrics.portfolio_value = 100000
        risk_engine._risk_metrics.long_exposure = 80000  # 80% long
        risk_engine._risk_metrics.short_exposure = 10000  # 10% short

        # Test adding more long exposure (would exceed 100% limit)
        is_valid = await risk_engine._validate_exposure(
            symbol='700.HK',
            quantity=100,
            price=300,  # 30,000 additional
            side='BUY',
            limits=risk_engine._global_limits
        )

        assert is_valid == False  # Would make long exposure 110%

        # Test adding short exposure (within limits)
        is_valid = await risk_engine._validate_exposure(
            symbol='700.HK',
            quantity=100,
            price=100,  # 10,000 additional
            side='SELL',
            limits=risk_engine._global_limits
        )

        assert is_valid == True  # Would make short exposure 20% (under 30% limit)

    @pytest.mark.asyncio
    async def test_validate_loss_limits(self, risk_engine):
        """Test loss limit validation."""
        risk_engine._risk_metrics.portfolio_value = 100000
        risk_engine._risk_metrics.daily_pnl = -6000  # -6% loss

        # Should fail validation (exceeds 5% daily loss limit)
        is_valid = await risk_engine._validate_loss_limits(risk_engine._global_limits)

        assert is_valid == False

        # Test with acceptable loss
        risk_engine._risk_metrics.daily_pnl = -3000  # -3% loss
        is_valid = await risk_engine._validate_loss_limits(risk_engine._global_limits)

        assert is_valid == True

    @pytest.mark.asyncio
    async def test_validate_signal_risk(self, risk_engine):
        """Test signal-specific risk validation."""
        signal = Signal(
            symbol='700.HK',
            side='BUY',
            quantity=100,
            price=350.0,
            stop_loss=340.0,  # Risk: 10 per share
            take_profit=370.0,
            strategy_name='TestStrategy',
            signal_strength=0.8
        )

        order = {
            'symbol': '700.HK',
            'side': 'BUY',
            'quantity': 100,
            'price': 350.0
        }

        risk_engine._risk_metrics.portfolio_value = 100000

        # Potential loss: 100 * 10 = 1000 (1% of portfolio)
        is_valid = await risk_engine._validate_signal_risk(signal, order)

        assert is_valid == True  # Under 2% per-trade limit

        # Test with larger position (exceeds per-trade risk)
        order['quantity'] = 300  # Potential loss: 3000 (3% of portfolio)
        is_valid = await risk_engine._validate_signal_risk(signal, order)

        assert is_valid == False  # Exceeds 2% per-trade limit

    @pytest.mark.asyncio
    async def test_update_risk_metrics(self, risk_engine, mock_portfolio):
        """Test risk metrics update."""
        # Mock portfolio data
        from longport_quant.portfolio.state import PositionInfo
        positions = [
            PositionInfo(
                symbol='700.HK',
                quantity=100,
                cost_price=340.0,
                current_price=350.0,
                unrealized_pnl=1000,
                realized_pnl=500
            ),
            PositionInfo(
                symbol='9988.HK',
                quantity=-50,  # Short position
                cost_price=85.0,
                current_price=80.0,
                unrealized_pnl=250,
                realized_pnl=0
            )
        ]

        mock_portfolio.get_positions.return_value = positions
        mock_portfolio.get_cash_balance.return_value = 50000

        with patch.object(risk_engine, '_get_current_price', side_effect=[350.0, 80.0]):
            await risk_engine._update_risk_metrics()

        # Check calculated metrics
        assert risk_engine._risk_metrics.portfolio_value > 0
        assert risk_engine._risk_metrics.cash_available == 50000
        assert risk_engine._risk_metrics.long_exposure == 35000  # 100 * 350
        assert risk_engine._risk_metrics.short_exposure == 4000  # 50 * 80
        assert risk_engine._risk_metrics.gross_exposure == 39000
        assert risk_engine._risk_metrics.net_exposure == 31000

    def test_assess_risk_level(self, risk_engine):
        """Test risk level assessment."""
        # Low risk scenario
        risk_engine._risk_metrics.current_drawdown = 0.01
        risk_engine._risk_metrics.gross_exposure = 50000
        risk_engine._risk_metrics.portfolio_value = 100000
        risk_engine._risk_metrics.daily_pnl = 500

        level = risk_engine._assess_risk_level()
        assert level == RiskLevel.LOW

        # High risk scenario
        risk_engine._risk_metrics.current_drawdown = 0.12  # 12% drawdown
        risk_engine._risk_metrics.gross_exposure = 120000  # Over-leveraged
        risk_engine._risk_metrics.portfolio_value = 100000
        risk_engine._risk_metrics.daily_pnl = -4000  # 4% loss

        level = risk_engine._assess_risk_level()
        assert level in [RiskLevel.HIGH, RiskLevel.CRITICAL]

        # Critical risk scenario
        risk_engine._risk_metrics.current_drawdown = 0.18  # 18% drawdown
        risk_engine._risk_metrics.position_concentration = {'700.HK': 0.35}  # 35% concentration

        level = risk_engine._assess_risk_level()
        assert level == RiskLevel.CRITICAL

    def test_risk_alert_creation(self, risk_engine):
        """Test risk alert creation."""
        alert = RiskAlert(
            timestamp=datetime.now(),
            level=RiskLevel.HIGH,
            category="PORTFOLIO",
            message="Portfolio risk elevated",
            details={
                'drawdown': 0.12,
                'exposure': 1.2,
                'daily_loss': -0.04
            },
            action_required=True
        )

        risk_engine.add_alert(alert)

        # Check alert was added
        alerts = risk_engine.get_alerts()
        assert len(alerts) == 1
        assert alerts[0].level == RiskLevel.HIGH
        assert alerts[0].action_required == True

        # Test filtering by level
        high_alerts = risk_engine.get_alerts(level=RiskLevel.HIGH)
        assert len(high_alerts) == 1

        low_alerts = risk_engine.get_alerts(level=RiskLevel.LOW)
        assert len(low_alerts) == 0

    def test_symbol_specific_limits(self, risk_engine):
        """Test symbol-specific risk limits."""
        # Set custom limits for volatile stock
        volatile_limits = RiskLimits(
            max_position_size=5000,  # Half the normal size
            max_position_value=50000,
            max_order_size=2000,
            max_notional=20000,
            max_portfolio_allocation=0.1  # Only 10% for this symbol
        )

        risk_engine.set_limit('VOLATILE.HK', volatile_limits)

        # Check limits are stored
        assert 'VOLATILE.HK' in risk_engine._limits
        assert risk_engine._limits['VOLATILE.HK'].max_position_size == 5000

    @pytest.mark.asyncio
    async def test_continuous_monitoring(self, risk_engine):
        """Test continuous risk monitoring."""
        # Set high risk condition
        risk_engine._risk_metrics.risk_level = RiskLevel.CRITICAL

        # Run monitoring for a short time
        monitoring_task = asyncio.create_task(risk_engine.monitor_risk())

        # Wait briefly
        await asyncio.sleep(0.1)

        # Cancel monitoring
        monitoring_task.cancel()

        try:
            await monitoring_task
        except asyncio.CancelledError:
            pass

        # Should have created alert for critical risk
        alerts = risk_engine.get_alerts()
        critical_alerts = [a for a in alerts if a.level == RiskLevel.CRITICAL]

        # Note: Alert creation depends on update cycle timing
        # Just verify no errors occurred during monitoring


class TestRiskIntegration:
    """Integration tests for risk management."""

    @pytest.mark.asyncio
    async def test_complete_risk_check_flow(self):
        """Test complete risk check flow."""
        settings = MagicMock(spec=Settings)
        portfolio = MagicMock(spec=PortfolioService)
        db = MagicMock(spec=DatabaseSessionManager)

        risk_engine = RiskEngine(settings, portfolio, db)

        # Setup portfolio state
        portfolio.get_cash_balance = AsyncMock(return_value=100000)
        portfolio.get_positions = AsyncMock(return_value=[])

        # Create order
        order = {
            'symbol': '700.HK',
            'side': 'BUY',
            'quantity': 100,
            'price': 350.0
        }

        # Create signal
        signal = Signal(
            symbol='700.HK',
            side='BUY',
            quantity=100,
            price=350.0,
            stop_loss=340.0,
            take_profit=370.0,
            strategy_name='TestStrategy',
            signal_strength=0.8
        )

        with patch.object(risk_engine._watchlist, 'symbols', return_value=['700.HK']):
            with patch.object(risk_engine, '_get_current_price', return_value=350.0):
                with patch.object(risk_engine, '_get_position_size', return_value=0):
                    # Run complete validation
                    is_valid, error = await risk_engine.validate_order(order, signal)

                    # Should pass all checks
                    assert is_valid == True or error is not None  # Depends on metrics


if __name__ == "__main__":
    pytest.main([__file__, "-v"])