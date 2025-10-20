"""Real-time monitoring dashboard for trading system."""

from __future__ import annotations

from typing import Dict, List, Optional, Any, Set
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
from enum import Enum
import asyncio
import json
from collections import defaultdict, deque

from loguru import logger
import pandas as pd
import numpy as np

from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import (
    Position, OrderRecord, TradingSignal,
    KlineDaily, RealtimeQuote
)
from longport_quant.risk.checks import RiskEngine, RiskLevel
from longport_quant.portfolio.state import PortfolioService
from longport_quant.signals.signal_manager import SignalManager
from sqlalchemy import select, and_, func, desc


class SystemStatus(Enum):
    """System operational status."""
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"
    MAINTENANCE = "maintenance"


class ComponentStatus(Enum):
    """Component health status."""
    HEALTHY = "healthy"
    WARNING = "warning"
    ERROR = "error"
    OFFLINE = "offline"


@dataclass
class SystemMetrics:
    """System-wide metrics."""
    # Performance metrics
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    disk_usage: float = 0.0
    network_latency: float = 0.0

    # Trading metrics
    active_strategies: int = 0
    active_positions: int = 0
    pending_orders: int = 0
    today_trades: int = 0
    today_pnl: float = 0.0

    # Data metrics
    data_feed_status: ComponentStatus = ComponentStatus.OFFLINE
    last_data_update: Optional[datetime] = None
    queue_size: int = 0
    processing_rate: float = 0.0  # messages per second

    # Error metrics
    error_count: int = 0
    warning_count: int = 0
    last_error: Optional[str] = None


@dataclass
class StrategyMetrics:
    """Strategy performance metrics."""
    name: str
    status: ComponentStatus
    signals_generated: int = 0
    signals_executed: int = 0
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    total_pnl: float = 0.0
    today_pnl: float = 0.0
    positions: int = 0
    last_signal: Optional[datetime] = None


@dataclass
class PositionMetrics:
    """Position tracking metrics."""
    symbol: str
    quantity: float
    entry_price: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float
    pnl_percent: float
    holding_period: timedelta
    risk_level: RiskLevel


@dataclass
class MarketMetrics:
    """Market overview metrics."""
    symbol: str
    last_price: float
    change_percent: float
    volume: int
    bid: float
    ask: float
    spread: float
    volatility: float
    signal_strength: float = 0.0
    last_update: Optional[datetime] = None


class MonitoringDashboard:
    """Central monitoring dashboard for the trading system."""

    def __init__(
        self,
        db: DatabaseSessionManager,
        risk_engine: RiskEngine,
        portfolio: PortfolioService,
        signal_manager: SignalManager
    ):
        """
        Initialize monitoring dashboard.

        Args:
            db: Database session manager
            risk_engine: Risk management engine
            portfolio: Portfolio service
            signal_manager: Signal manager
        """
        self.db = db
        self.risk_engine = risk_engine
        self.portfolio = portfolio
        self.signal_manager = signal_manager

        # System state
        self.system_status = SystemStatus.STOPPED
        self.system_metrics = SystemMetrics()

        # Component tracking
        self.component_status: Dict[str, ComponentStatus] = {}
        self.strategy_metrics: Dict[str, StrategyMetrics] = {}
        self.position_metrics: Dict[str, PositionMetrics] = {}
        self.market_metrics: Dict[str, MarketMetrics] = {}

        # Historical data
        self.pnl_history: deque = deque(maxlen=100)
        self.signal_history: deque = deque(maxlen=100)
        self.error_history: deque = deque(maxlen=50)

        # Alert configuration
        self.alert_rules: List[AlertRule] = self._init_alert_rules()
        self.active_alerts: List[Alert] = []

        # Monitoring tasks
        self._monitoring_tasks: List[asyncio.Task] = []

    async def start(self):
        """Start monitoring dashboard."""
        logger.info("Starting monitoring dashboard")
        self.system_status = SystemStatus.RUNNING

        # Start monitoring tasks
        self._monitoring_tasks = [
            asyncio.create_task(self._monitor_system()),
            asyncio.create_task(self._monitor_strategies()),
            asyncio.create_task(self._monitor_positions()),
            asyncio.create_task(self._monitor_market()),
            asyncio.create_task(self._monitor_risks()),
            asyncio.create_task(self._process_alerts())
        ]

        logger.info("Monitoring dashboard started")

    async def stop(self):
        """Stop monitoring dashboard."""
        logger.info("Stopping monitoring dashboard")
        self.system_status = SystemStatus.STOPPED

        # Cancel monitoring tasks
        for task in self._monitoring_tasks:
            task.cancel()

        await asyncio.gather(*self._monitoring_tasks, return_exceptions=True)
        self._monitoring_tasks.clear()

        logger.info("Monitoring dashboard stopped")

    async def _monitor_system(self):
        """Monitor system health and performance."""
        while self.system_status == SystemStatus.RUNNING:
            try:
                # Update system metrics
                await self._update_system_metrics()

                # Check component health
                await self._check_component_health()

                # Log system status
                if self.system_metrics.error_count > 0:
                    logger.warning(f"System errors: {self.system_metrics.error_count}")

                await asyncio.sleep(10)  # Check every 10 seconds

            except Exception as e:
                logger.error(f"Error monitoring system: {e}")
                await asyncio.sleep(10)

    async def _monitor_strategies(self):
        """Monitor strategy performance."""
        while self.system_status == SystemStatus.RUNNING:
            try:
                async with self.db.session() as session:
                    # Get strategy signals from last 24 hours
                    cutoff = datetime.now() - timedelta(days=1)
                    stmt = select(TradingSignal).where(
                        TradingSignal.created_at >= cutoff
                    )
                    result = await session.execute(stmt)
                    signals = result.scalars().all()

                    # Group by strategy
                    strategy_signals = defaultdict(list)
                    for signal in signals:
                        strategy_signals[signal.strategy_name].append(signal)

                    # Update metrics for each strategy
                    for strategy_name, strat_signals in strategy_signals.items():
                        if strategy_name not in self.strategy_metrics:
                            self.strategy_metrics[strategy_name] = StrategyMetrics(
                                name=strategy_name,
                                status=ComponentStatus.HEALTHY
                            )

                        metrics = self.strategy_metrics[strategy_name]
                        metrics.signals_generated = len(strat_signals)
                        metrics.signals_executed = sum(1 for s in strat_signals if s.executed)

                        if strat_signals:
                            metrics.last_signal = max(s.created_at for s in strat_signals)

                        # Calculate win rate (simplified)
                        executed_signals = [s for s in strat_signals if s.executed]
                        if executed_signals:
                            # This would need actual P&L tracking
                            metrics.win_rate = 0.5  # Placeholder

                await asyncio.sleep(30)  # Check every 30 seconds

            except Exception as e:
                logger.error(f"Error monitoring strategies: {e}")
                await asyncio.sleep(30)

    async def _monitor_positions(self):
        """Monitor open positions."""
        while self.system_status == SystemStatus.RUNNING:
            try:
                positions = await self.portfolio.get_positions()

                for position in positions:
                    # Get current price
                    current_price = await self._get_current_price(position.symbol)

                    # Calculate metrics
                    market_value = position.quantity * current_price
                    entry_value = position.quantity * position.cost_price
                    unrealized_pnl = market_value - entry_value
                    pnl_percent = (unrealized_pnl / entry_value * 100) if entry_value > 0 else 0

                    # Assess risk level
                    risk_level = self._assess_position_risk(pnl_percent)

                    # Update metrics
                    self.position_metrics[position.symbol] = PositionMetrics(
                        symbol=position.symbol,
                        quantity=position.quantity,
                        entry_price=position.cost_price,
                        current_price=current_price,
                        market_value=market_value,
                        unrealized_pnl=unrealized_pnl,
                        realized_pnl=position.realized_pnl or 0,
                        pnl_percent=pnl_percent,
                        holding_period=timedelta(days=1),  # Placeholder
                        risk_level=risk_level
                    )

                    # Alert on high risk positions
                    if risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
                        await self._create_alert(
                            level="WARNING",
                            category="POSITION",
                            message=f"High risk position: {position.symbol} ({pnl_percent:.1f}% loss)",
                            details={"symbol": position.symbol, "pnl": unrealized_pnl}
                        )

                # Update total P&L
                total_unrealized = sum(p.unrealized_pnl for p in self.position_metrics.values())
                self.system_metrics.today_pnl = total_unrealized
                self.pnl_history.append((datetime.now(), total_unrealized))

                await asyncio.sleep(15)  # Check every 15 seconds

            except Exception as e:
                logger.error(f"Error monitoring positions: {e}")
                await asyncio.sleep(15)

    async def _monitor_market(self):
        """Monitor market conditions."""
        while self.system_status == SystemStatus.RUNNING:
            try:
                # Get watched symbols
                symbols = list(self.position_metrics.keys())

                for symbol in symbols:
                    # Get latest quote
                    async with self.db.session() as session:
                        stmt = select(RealtimeQuote).where(
                            RealtimeQuote.symbol == symbol
                        ).order_by(RealtimeQuote.timestamp.desc()).limit(1)

                        result = await session.execute(stmt)
                        quote = result.scalar_one_or_none()

                        if quote:
                            # Calculate metrics
                            change_pct = ((float(quote.last_done) - float(quote.prev_close)) /
                                         float(quote.prev_close) * 100) if quote.prev_close else 0

                            spread = (float(quote.ask_price) - float(quote.bid_price)) if quote.bid_price and quote.ask_price else 0

                            # Get signal strength from signal manager
                            active_signals = await self.signal_manager.get_active_signals(symbol)
                            avg_strength = (sum(s.strength for s in active_signals) / len(active_signals)) if active_signals else 0

                            # Update market metrics
                            self.market_metrics[symbol] = MarketMetrics(
                                symbol=symbol,
                                last_price=float(quote.last_done) if quote.last_done else 0,
                                change_percent=change_pct,
                                volume=quote.volume or 0,
                                bid=float(quote.bid_price) if quote.bid_price else 0,
                                ask=float(quote.ask_price) if quote.ask_price else 0,
                                spread=spread,
                                volatility=0,  # Would calculate from historical data
                                signal_strength=avg_strength,
                                last_update=quote.timestamp
                            )

                await asyncio.sleep(10)  # Check every 10 seconds

            except Exception as e:
                logger.error(f"Error monitoring market: {e}")
                await asyncio.sleep(10)

    async def _monitor_risks(self):
        """Monitor risk metrics."""
        while self.system_status == SystemStatus.RUNNING:
            try:
                # Get risk metrics from risk engine
                risk_metrics = self.risk_engine.get_risk_metrics()

                # Check for risk alerts
                if risk_metrics.risk_level == RiskLevel.CRITICAL:
                    await self._create_alert(
                        level="CRITICAL",
                        category="RISK",
                        message=f"Critical risk level detected",
                        details={
                            "drawdown": risk_metrics.current_drawdown,
                            "gross_exposure": risk_metrics.gross_exposure,
                            "var_95": risk_metrics.var_95
                        }
                    )
                elif risk_metrics.risk_level == RiskLevel.HIGH:
                    await self._create_alert(
                        level="WARNING",
                        category="RISK",
                        message=f"High risk level detected",
                        details={
                            "drawdown": risk_metrics.current_drawdown,
                            "gross_exposure": risk_metrics.gross_exposure
                        }
                    )

                # Check specific risk conditions
                if risk_metrics.current_drawdown > 0.1:  # 10% drawdown
                    await self._create_alert(
                        level="WARNING",
                        category="DRAWDOWN",
                        message=f"Significant drawdown: {risk_metrics.current_drawdown:.1%}",
                        details={"drawdown": risk_metrics.current_drawdown}
                    )

                if risk_metrics.daily_pnl < -10000:  # Daily loss > $10k
                    await self._create_alert(
                        level="WARNING",
                        category="LOSS",
                        message=f"Large daily loss: ${abs(risk_metrics.daily_pnl):,.2f}",
                        details={"daily_pnl": risk_metrics.daily_pnl}
                    )

                await asyncio.sleep(30)  # Check every 30 seconds

            except Exception as e:
                logger.error(f"Error monitoring risks: {e}")
                await asyncio.sleep(30)

    async def _process_alerts(self):
        """Process and manage alerts."""
        while self.system_status == SystemStatus.RUNNING:
            try:
                # Clean up old alerts
                cutoff = datetime.now() - timedelta(hours=24)
                self.active_alerts = [a for a in self.active_alerts
                                     if a.timestamp > cutoff]

                # Check alert rules
                for rule in self.alert_rules:
                    if await rule.check(self):
                        await self._create_alert(
                            level=rule.level,
                            category=rule.category,
                            message=rule.message,
                            details=rule.get_details(self)
                        )

                await asyncio.sleep(60)  # Check every minute

            except Exception as e:
                logger.error(f"Error processing alerts: {e}")
                await asyncio.sleep(60)

    async def _update_system_metrics(self):
        """Update system performance metrics."""
        try:
            # Get basic system info (simplified)
            import psutil

            self.system_metrics.cpu_usage = psutil.cpu_percent()
            self.system_metrics.memory_usage = psutil.virtual_memory().percent
            self.system_metrics.disk_usage = psutil.disk_usage('/').percent

            # Get trading metrics
            async with self.db.session() as session:
                # Today's trades
                today = datetime.now().date()
                stmt = select(func.count(OrderRecord.id)).where(
                    and_(
                        OrderRecord.created_at >= datetime.combine(today, datetime.min.time()),
                        OrderRecord.status.in_(["FILLED", "PARTIAL"])
                    )
                )
                result = await session.execute(stmt)
                self.system_metrics.today_trades = result.scalar() or 0

                # Active positions
                positions = await self.portfolio.get_positions()
                self.system_metrics.active_positions = len(positions)

                # Pending orders
                stmt = select(func.count(OrderRecord.id)).where(
                    OrderRecord.status.in_(["PENDING", "SUBMITTED"])
                )
                result = await session.execute(stmt)
                self.system_metrics.pending_orders = result.scalar() or 0

        except Exception as e:
            logger.error(f"Error updating system metrics: {e}")

    async def _check_component_health(self):
        """Check health of system components."""
        # Check database connection
        try:
            async with self.db.session() as session:
                await session.execute(select(1))
            self.component_status["database"] = ComponentStatus.HEALTHY
        except Exception:
            self.component_status["database"] = ComponentStatus.ERROR

        # Check data feed
        if self.system_metrics.last_data_update:
            age = (datetime.now() - self.system_metrics.last_data_update).seconds
            if age < 60:
                self.component_status["data_feed"] = ComponentStatus.HEALTHY
            elif age < 300:
                self.component_status["data_feed"] = ComponentStatus.WARNING
            else:
                self.component_status["data_feed"] = ComponentStatus.ERROR
        else:
            self.component_status["data_feed"] = ComponentStatus.OFFLINE

        # Check risk engine
        if self.risk_engine:
            self.component_status["risk_engine"] = ComponentStatus.HEALTHY
        else:
            self.component_status["risk_engine"] = ComponentStatus.ERROR

        # Update overall data feed status
        self.system_metrics.data_feed_status = self.component_status.get(
            "data_feed", ComponentStatus.OFFLINE
        )

    async def _get_current_price(self, symbol: str) -> float:
        """Get current price for symbol."""
        try:
            async with self.db.session() as session:
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

    def _assess_position_risk(self, pnl_percent: float) -> RiskLevel:
        """Assess risk level of a position."""
        if pnl_percent < -10:
            return RiskLevel.CRITICAL
        elif pnl_percent < -5:
            return RiskLevel.HIGH
        elif pnl_percent < -2:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW

    async def _create_alert(
        self,
        level: str,
        category: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        """Create a new alert."""
        alert = Alert(
            timestamp=datetime.now(),
            level=level,
            category=category,
            message=message,
            details=details or {}
        )

        self.active_alerts.append(alert)

        # Log based on level
        if level == "CRITICAL":
            logger.critical(f"ALERT: {message}")
        elif level == "WARNING":
            logger.warning(f"ALERT: {message}")
        else:
            logger.info(f"ALERT: {message}")

        # Update error counts
        if level in ["CRITICAL", "ERROR"]:
            self.system_metrics.error_count += 1
            self.system_metrics.last_error = message
        elif level == "WARNING":
            self.system_metrics.warning_count += 1

    def _init_alert_rules(self) -> List[AlertRule]:
        """Initialize alert rules."""
        rules = []

        # High CPU usage
        rules.append(AlertRule(
            name="high_cpu",
            condition=lambda d: d.system_metrics.cpu_usage > 80,
            level="WARNING",
            category="SYSTEM",
            message="High CPU usage detected"
        ))

        # High memory usage
        rules.append(AlertRule(
            name="high_memory",
            condition=lambda d: d.system_metrics.memory_usage > 85,
            level="WARNING",
            category="SYSTEM",
            message="High memory usage detected"
        ))

        # No recent data
        rules.append(AlertRule(
            name="stale_data",
            condition=lambda d: (
                d.system_metrics.last_data_update and
                (datetime.now() - d.system_metrics.last_data_update).seconds > 300
            ),
            level="WARNING",
            category="DATA",
            message="No recent market data received"
        ))

        return rules

    def get_dashboard_data(self) -> Dict[str, Any]:
        """Get dashboard data for display."""
        return {
            "system": {
                "status": self.system_status.value,
                "cpu": self.system_metrics.cpu_usage,
                "memory": self.system_metrics.memory_usage,
                "disk": self.system_metrics.disk_usage,
                "uptime": datetime.now().isoformat()
            },
            "trading": {
                "active_positions": self.system_metrics.active_positions,
                "pending_orders": self.system_metrics.pending_orders,
                "today_trades": self.system_metrics.today_trades,
                "today_pnl": self.system_metrics.today_pnl
            },
            "components": {
                name: status.value
                for name, status in self.component_status.items()
            },
            "strategies": {
                name: {
                    "status": metrics.status.value,
                    "signals": metrics.signals_generated,
                    "executed": metrics.signals_executed,
                    "pnl": metrics.today_pnl
                }
                for name, metrics in self.strategy_metrics.items()
            },
            "positions": {
                symbol: {
                    "quantity": metrics.quantity,
                    "pnl": metrics.unrealized_pnl,
                    "pnl_percent": metrics.pnl_percent,
                    "risk": metrics.risk_level.value
                }
                for symbol, metrics in self.position_metrics.items()
            },
            "market": {
                symbol: {
                    "price": metrics.last_price,
                    "change": metrics.change_percent,
                    "volume": metrics.volume,
                    "signal": metrics.signal_strength
                }
                for symbol, metrics in self.market_metrics.items()
            },
            "alerts": [
                {
                    "timestamp": alert.timestamp.isoformat(),
                    "level": alert.level,
                    "message": alert.message
                }
                for alert in self.active_alerts[-10:]  # Last 10 alerts
            ]
        }

    def get_performance_summary(self) -> Dict[str, Any]:
        """Get performance summary."""
        risk_metrics = self.risk_engine.get_risk_metrics()

        return {
            "portfolio_value": risk_metrics.portfolio_value,
            "cash": risk_metrics.cash_available,
            "exposure": {
                "long": risk_metrics.long_exposure,
                "short": risk_metrics.short_exposure,
                "gross": risk_metrics.gross_exposure,
                "net": risk_metrics.net_exposure
            },
            "risk": {
                "level": risk_metrics.risk_level.value,
                "drawdown": risk_metrics.current_drawdown,
                "var_95": risk_metrics.var_95,
                "sharpe": risk_metrics.sharpe_ratio
            },
            "pnl": {
                "today": self.system_metrics.today_pnl,
                "history": list(self.pnl_history)[-20:]  # Last 20 data points
            }
        }


@dataclass
class Alert:
    """System alert."""
    timestamp: datetime
    level: str  # INFO, WARNING, ERROR, CRITICAL
    category: str
    message: str
    details: Dict[str, Any]


@dataclass
class AlertRule:
    """Alert generation rule."""
    name: str
    condition: Any  # Callable that returns bool
    level: str
    category: str
    message: str

    async def check(self, dashboard: MonitoringDashboard) -> bool:
        """Check if alert should be triggered."""
        try:
            return self.condition(dashboard)
        except Exception:
            return False

    def get_details(self, dashboard: MonitoringDashboard) -> Dict[str, Any]:
        """Get alert details."""
        return {
            "rule": self.name,
            "timestamp": datetime.now().isoformat()
        }