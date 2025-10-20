"""Enhanced risk validation and management system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from enum import Enum
import asyncio

from loguru import logger
import numpy as np
import pandas as pd

from longport_quant.config.settings import Settings
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.portfolio.state import PortfolioService
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import Position, OrderRecord, KlineDaily
from longport_quant.common.types import Signal
from sqlalchemy import select, and_


class RiskLevel(Enum):
    """Risk levels for monitoring."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RiskLimits:
    """Risk limits configuration."""
    # Position limits
    max_position_size: float  # Maximum position in shares
    max_position_value: float  # Maximum position value in currency

    # Order limits
    max_order_size: float  # Maximum order size
    max_notional: float  # Maximum order notional value

    # Optional parameters with defaults
    max_portfolio_allocation: float = 0.2  # Max 20% of portfolio per position
    max_daily_trades: int = 100  # Maximum trades per day
    max_loss_per_trade: float = 0.02  # Max 2% loss per trade
    max_daily_loss: float = 0.05  # Max 5% daily loss
    max_drawdown: float = 0.15  # Max 15% drawdown

    # Exposure limits
    max_long_exposure: float = 1.0  # Max 100% long
    max_short_exposure: float = 0.3  # Max 30% short
    max_gross_exposure: float = 1.3  # Max 130% gross
    max_concentration: float = 0.3  # Max 30% in single position


@dataclass
class RiskMetrics:
    """Current risk metrics."""
    portfolio_value: float = 0.0
    cash_available: float = 0.0
    long_exposure: float = 0.0
    short_exposure: float = 0.0
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    current_drawdown: float = 0.0
    daily_pnl: float = 0.0
    daily_trades: int = 0
    var_95: float = 0.0  # Value at Risk (95% confidence)
    sharpe_ratio: float = 0.0
    position_concentration: Dict[str, float] = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.LOW


@dataclass
class RiskAlert:
    """Risk alert notification."""
    timestamp: datetime
    level: RiskLevel
    category: str
    message: str
    details: Dict[str, Any]
    action_required: bool = False


class RiskEngine:
    """Enhanced risk management engine."""

    def __init__(
        self,
        settings: Settings,
        portfolio: PortfolioService,
        db: DatabaseSessionManager
    ):
        """
        Initialize risk engine.

        Args:
            settings: Application settings
            portfolio: Portfolio service
            db: Database session manager
        """
        self._settings = settings
        self._portfolio = portfolio
        self._db = db
        self._limits: Dict[str, RiskLimits] = {}
        self._global_limits = self._init_global_limits()
        self._watchlist = WatchlistLoader().load()
        self._risk_metrics = RiskMetrics()
        self._alerts: List[RiskAlert] = []
        self._high_water_mark = 0.0

    def _init_global_limits(self) -> RiskLimits:
        """Initialize global risk limits from settings."""
        return RiskLimits(
            max_position_size=10000,
            max_position_value=100000,
            max_portfolio_allocation=0.2,
            max_order_size=5000,
            max_notional=50000,
            max_daily_trades=100,
            max_loss_per_trade=0.02,
            max_daily_loss=0.05,
            max_drawdown=0.15,
            max_long_exposure=1.0,
            max_short_exposure=0.3,
            max_gross_exposure=1.3,
            max_concentration=0.3
        )

    async def validate_order(
        self,
        order: Dict[str, Any],
        signal: Optional[Signal] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Comprehensive order validation.

        Args:
            order: Order details
            signal: Associated signal

        Returns:
            Tuple of (is_valid, error_message)
        """
        symbol = order.get("symbol")

        # Check if symbol is in watchlist
        if symbol not in self._watchlist.symbols():
            return False, f"Symbol {symbol} not in watchlist"

        # Get symbol-specific or global limits
        limits = self._limits.get(symbol, self._global_limits)

        # Update current metrics
        await self._update_risk_metrics()

        # Validate order size
        quantity = float(order.get("quantity", 0))
        if quantity > limits.max_order_size:
            return False, f"Order size {quantity} exceeds limit {limits.max_order_size}"

        # Validate notional value
        price = float(order.get("price", 0))
        if price <= 0:
            # Get current market price if not provided
            price = await self._get_current_price(symbol)

        notional = quantity * price
        if notional > limits.max_notional:
            return False, f"Notional value {notional:.2f} exceeds limit {limits.max_notional:.2f}"

        # Check portfolio allocation
        portfolio_pct = notional / self._risk_metrics.portfolio_value if self._risk_metrics.portfolio_value > 0 else 1
        if portfolio_pct > limits.max_portfolio_allocation:
            return False, f"Position would be {portfolio_pct:.1%} of portfolio, exceeds limit {limits.max_portfolio_allocation:.1%}"

        # Check position limits
        current_position = await self._get_position_size(symbol)
        new_position = current_position + quantity if order.get("side") == "BUY" else current_position - quantity

        if abs(new_position) > limits.max_position_size:
            return False, f"Position size {abs(new_position)} would exceed limit {limits.max_position_size}"

        # Check exposure limits
        side = order.get("side")
        if not await self._validate_exposure(symbol, quantity, price, side, limits):
            return False, "Order would exceed exposure limits"

        # Check loss limits
        if not await self._validate_loss_limits(limits):
            return False, "Portfolio exceeds loss limits"

        # Check daily trade count
        if self._risk_metrics.daily_trades >= limits.max_daily_trades:
            return False, f"Daily trade limit reached ({limits.max_daily_trades})"

        # Check drawdown
        if self._risk_metrics.current_drawdown > limits.max_drawdown:
            return False, f"Current drawdown {self._risk_metrics.current_drawdown:.1%} exceeds limit"

        # Additional signal-based validation
        if signal:
            if not await self._validate_signal_risk(signal, order):
                return False, "Signal risk validation failed"

        logger.info(f"Order validated: {symbol} {side} {quantity} @ {price:.2f}")
        return True, None

    async def _validate_exposure(
        self,
        symbol: str,
        quantity: float,
        price: float,
        side: str,
        limits: RiskLimits
    ) -> bool:
        """Validate exposure limits."""
        # Calculate new exposure
        order_value = quantity * price

        if side == "BUY":
            new_long_exposure = self._risk_metrics.long_exposure + order_value
            new_short_exposure = self._risk_metrics.short_exposure
        else:
            new_long_exposure = self._risk_metrics.long_exposure
            new_short_exposure = self._risk_metrics.short_exposure + order_value

        new_gross_exposure = new_long_exposure + new_short_exposure
        portfolio_value = self._risk_metrics.portfolio_value

        if portfolio_value <= 0:
            return False

        # Check long exposure
        if new_long_exposure / portfolio_value > limits.max_long_exposure:
            logger.warning(f"Long exposure would exceed limit: {new_long_exposure/portfolio_value:.1%}")
            return False

        # Check short exposure
        if new_short_exposure / portfolio_value > limits.max_short_exposure:
            logger.warning(f"Short exposure would exceed limit: {new_short_exposure/portfolio_value:.1%}")
            return False

        # Check gross exposure
        if new_gross_exposure / portfolio_value > limits.max_gross_exposure:
            logger.warning(f"Gross exposure would exceed limit: {new_gross_exposure/portfolio_value:.1%}")
            return False

        return True

    async def _validate_loss_limits(self, limits: RiskLimits) -> bool:
        """Validate loss limits."""
        # Check daily loss
        portfolio_value = self._risk_metrics.portfolio_value
        if portfolio_value > 0 and self._risk_metrics.daily_pnl < 0:
            daily_loss_pct = abs(self._risk_metrics.daily_pnl) / portfolio_value
            if daily_loss_pct > limits.max_daily_loss:
                logger.warning(f"Daily loss {daily_loss_pct:.1%} exceeds limit {limits.max_daily_loss:.1%}")
                return False

        return True

    async def _validate_signal_risk(self, signal: Signal, order: Dict[str, Any]) -> bool:
        """Validate signal-specific risk."""
        # Check if stop loss is set
        if not signal.stop_loss:
            logger.warning("Signal has no stop loss")
            return False

        # Calculate potential loss
        entry_price = float(order.get("price", signal.price_target))
        stop_loss = signal.stop_loss
        quantity = float(order.get("quantity", 0))

        if order.get("side") == "BUY":
            potential_loss = max(0, (entry_price - stop_loss) * quantity)
        else:
            potential_loss = max(0, (stop_loss - entry_price) * quantity)

        # Check if potential loss is acceptable
        portfolio_value = self._risk_metrics.portfolio_value
        if portfolio_value > 0:
            loss_pct = potential_loss / portfolio_value
            if loss_pct > self._global_limits.max_loss_per_trade:
                logger.warning(f"Potential loss {loss_pct:.1%} exceeds per-trade limit")
                return False

        return True

    async def _update_risk_metrics(self) -> None:
        """Update current risk metrics."""
        try:
            # Get portfolio value and positions
            positions = await self._portfolio.get_positions()
            cash = await self._portfolio.get_cash_balance()

            portfolio_value = cash
            long_exposure = 0.0
            short_exposure = 0.0
            position_values = {}

            for position in positions:
                market_value = position.quantity * await self._get_current_price(position.symbol)
                portfolio_value += market_value

                if position.quantity > 0:
                    long_exposure += market_value
                else:
                    short_exposure += abs(market_value)

                position_values[position.symbol] = market_value

            # Calculate metrics
            self._risk_metrics.portfolio_value = portfolio_value
            self._risk_metrics.cash_available = cash
            self._risk_metrics.long_exposure = long_exposure
            self._risk_metrics.short_exposure = short_exposure
            self._risk_metrics.gross_exposure = long_exposure + short_exposure
            self._risk_metrics.net_exposure = long_exposure - short_exposure

            # Calculate concentration
            if portfolio_value > 0:
                for symbol, value in position_values.items():
                    self._risk_metrics.position_concentration[symbol] = value / portfolio_value

            # Update drawdown
            if portfolio_value > self._high_water_mark:
                self._high_water_mark = portfolio_value

            if self._high_water_mark > 0:
                self._risk_metrics.current_drawdown = (self._high_water_mark - portfolio_value) / self._high_water_mark

            # Update daily P&L
            await self._update_daily_pnl()

            # Calculate VaR
            self._risk_metrics.var_95 = await self._calculate_var()

            # Determine risk level
            self._risk_metrics.risk_level = self._assess_risk_level()

        except Exception as e:
            logger.error(f"Error updating risk metrics: {e}")

    async def _update_daily_pnl(self) -> None:
        """Update daily P&L."""
        try:
            # Get today's trades
            today = datetime.now().date()
            async with self._db.session() as session:
                stmt = select(OrderRecord).where(
                    and_(
                        OrderRecord.created_at >= datetime.combine(today, datetime.min.time()),
                        OrderRecord.status.in_(["FILLED", "PARTIAL"])
                    )
                )
                result = await session.execute(stmt)
                orders = result.scalars().all()

            self._risk_metrics.daily_trades = len(orders)

            # Calculate P&L (simplified - in production would track actual fills)
            daily_pnl = 0.0
            for order in orders:
                # This is simplified - actual implementation would track entry/exit
                pass

            self._risk_metrics.daily_pnl = daily_pnl

        except Exception as e:
            logger.error(f"Error updating daily P&L: {e}")

    async def _calculate_var(self, confidence: float = 0.95) -> float:
        """Calculate Value at Risk."""
        try:
            # Get historical returns
            returns = await self._get_historical_returns()

            if len(returns) < 20:
                return 0.0

            # Calculate VaR using historical method
            var_percentile = (1 - confidence) * 100
            var = np.percentile(returns, var_percentile)

            # Scale to portfolio value
            return abs(var) * self._risk_metrics.portfolio_value

        except Exception as e:
            logger.error(f"Error calculating VaR: {e}")
            return 0.0

    async def _get_historical_returns(self, days: int = 252) -> List[float]:
        """Get historical portfolio returns."""
        # Simplified - in production would track actual portfolio returns
        return np.random.normal(0.001, 0.02, days).tolist()

    def _assess_risk_level(self) -> RiskLevel:
        """Assess current risk level."""
        score = 0

        # Check drawdown
        if self._risk_metrics.current_drawdown > 0.1:
            score += 3
        elif self._risk_metrics.current_drawdown > 0.05:
            score += 2
        elif self._risk_metrics.current_drawdown > 0.02:
            score += 1

        # Check exposure
        if self._risk_metrics.gross_exposure > self._risk_metrics.portfolio_value:
            score += 2

        # Check concentration
        max_concentration = max(self._risk_metrics.position_concentration.values()) if self._risk_metrics.position_concentration else 0
        if max_concentration > 0.25:
            score += 2
        elif max_concentration > 0.15:
            score += 1

        # Check daily loss
        if self._risk_metrics.daily_pnl < 0:
            daily_loss_pct = abs(self._risk_metrics.daily_pnl) / self._risk_metrics.portfolio_value if self._risk_metrics.portfolio_value > 0 else 0
            if daily_loss_pct > 0.03:
                score += 2
            elif daily_loss_pct > 0.01:
                score += 1

        # Determine level
        if score >= 6:
            return RiskLevel.CRITICAL
        elif score >= 4:
            return RiskLevel.HIGH
        elif score >= 2:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW

    async def _get_current_price(self, symbol: str) -> float:
        """Get current market price."""
        try:
            async with self._db.session() as session:
                stmt = select(KlineDaily).where(
                    KlineDaily.symbol == symbol
                ).order_by(KlineDaily.trade_date.desc()).limit(1)

                result = await session.execute(stmt)
                kline = result.scalar_one_or_none()

                if kline:
                    return float(kline.close)

        except Exception as e:
            logger.error(f"Error getting price for {symbol}: {e}")

        return 0.0

    async def _get_position_size(self, symbol: str) -> float:
        """Get current position size."""
        positions = await self._portfolio.get_positions()
        for position in positions:
            if position.symbol == symbol:
                return position.quantity
        return 0.0

    def set_limit(self, symbol: str, limits: RiskLimits) -> None:
        """Set symbol-specific risk limits."""
        self._limits[symbol] = limits
        logger.info(f"Set risk limits for {symbol}")

    def get_risk_metrics(self) -> RiskMetrics:
        """Get current risk metrics."""
        return self._risk_metrics

    def get_alerts(self, level: Optional[RiskLevel] = None) -> List[RiskAlert]:
        """Get risk alerts."""
        if level:
            return [a for a in self._alerts if a.level == level]
        return self._alerts

    def add_alert(self, alert: RiskAlert) -> None:
        """Add a risk alert."""
        self._alerts.append(alert)
        logger.warning(f"Risk alert: {alert.level.value} - {alert.message}")

    async def monitor_risk(self) -> None:
        """Continuous risk monitoring."""
        while True:
            try:
                await self._update_risk_metrics()

                # Check for risk conditions
                if self._risk_metrics.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
                    alert = RiskAlert(
                        timestamp=datetime.now(),
                        level=self._risk_metrics.risk_level,
                        category="PORTFOLIO",
                        message=f"Portfolio risk level: {self._risk_metrics.risk_level.value}",
                        details={
                            "drawdown": self._risk_metrics.current_drawdown,
                            "gross_exposure": self._risk_metrics.gross_exposure,
                            "daily_pnl": self._risk_metrics.daily_pnl
                        },
                        action_required=True
                    )
                    self.add_alert(alert)

                # Sleep before next check
                await asyncio.sleep(60)  # Check every minute

            except Exception as e:
                logger.error(f"Error in risk monitoring: {e}")
                await asyncio.sleep(60)
