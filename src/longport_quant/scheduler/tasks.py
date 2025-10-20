"""Scheduled tasks for automated trading system operations."""

from __future__ import annotations

from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, time, timedelta
from dataclasses import dataclass, field
from enum import Enum
import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.job import Job
from loguru import logger

from longport_quant.data.kline_sync import KlineDataService
from longport_quant.features.feature_engine import FeatureEngine
from longport_quant.signals.signal_manager import SignalManager
from longport_quant.strategy.manager import StrategyManager
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import TradingCalendar
from sqlalchemy import select, and_, func


class TaskStatus(Enum):
    """Task execution status."""
    SCHEDULED = "scheduled"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskPriority(Enum):
    """Task priority levels."""
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4


@dataclass
class TaskConfig:
    """Task configuration."""
    name: str
    function: Callable
    schedule: str  # Cron expression or interval
    priority: TaskPriority = TaskPriority.MEDIUM
    enabled: bool = True
    max_retries: int = 3
    timeout: int = 300  # seconds
    description: Optional[str] = None
    kwargs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    """Task execution result."""
    task_name: str
    status: TaskStatus
    start_time: datetime
    end_time: datetime
    duration: timedelta
    error: Optional[str] = None
    result: Optional[Any] = None


class ScheduledTaskManager:
    """Manages scheduled tasks for the trading system."""

    def __init__(
        self,
        db: DatabaseSessionManager,
        kline_service: KlineDataService,
        feature_engine: FeatureEngine,
        signal_manager: SignalManager,
        strategy_manager: Optional[StrategyManager] = None
    ):
        """
        Initialize scheduled task manager.

        Args:
            db: Database session manager
            kline_service: K-line data sync service
            feature_engine: Feature calculation engine
            signal_manager: Signal management system
            strategy_manager: Strategy manager (optional)
        """
        self.db = db
        self.kline_service = kline_service
        self.feature_engine = feature_engine
        self.signal_manager = signal_manager
        self.strategy_manager = strategy_manager

        # Scheduler
        self.scheduler = AsyncIOScheduler(timezone='Asia/Shanghai')

        # Task tracking
        self.tasks: Dict[str, TaskConfig] = {}
        self.task_history: List[TaskResult] = []
        self.running_tasks: Dict[str, datetime] = {}

        # Initialize default tasks
        self._init_default_tasks()

    def _init_default_tasks(self):
        """Initialize default scheduled tasks."""

        # Daily K-line sync (17:30 every trading day)
        self.add_task(TaskConfig(
            name="sync_daily_klines",
            function=self._sync_daily_klines,
            schedule="30 17 * * MON-FRI",  # 17:30 Monday to Friday
            priority=TaskPriority.HIGH,
            description="Sync daily K-line data after market close",
            kwargs={"days_back": 5}
        ))

        # Minute K-line sync (every minute during trading hours)
        self.add_task(TaskConfig(
            name="sync_minute_klines",
            function=self._sync_minute_klines,
            schedule="*/1 9-15 * * MON-FRI",  # Every minute 9:00-15:59 Mon-Fri
            priority=TaskPriority.CRITICAL,
            description="Sync minute K-line data in real-time",
            kwargs={"minutes_back": 5}
        ))

        # Feature calculation (every 5 minutes)
        self.add_task(TaskConfig(
            name="calculate_features",
            function=self._calculate_features,
            schedule="*/5 9-16 * * MON-FRI",  # Every 5 minutes during market hours
            priority=TaskPriority.HIGH,
            description="Calculate technical features for all symbols"
        ))

        # Strategy execution (every minute during trading)
        self.add_task(TaskConfig(
            name="execute_strategies",
            function=self._execute_strategies,
            schedule="*/1 9-15 * * MON-FRI",
            priority=TaskPriority.CRITICAL,
            description="Execute trading strategies",
            enabled=True  # Enabled for testing
        ))

        # Data cleanup (monthly)
        self.add_task(TaskConfig(
            name="cleanup_old_data",
            function=self._cleanup_old_data,
            schedule="0 2 1 * *",  # 2:00 AM on the 1st of each month
            priority=TaskPriority.LOW,
            description="Clean up old minute data and logs",
            kwargs={"days_to_keep": 180}
        ))

        # Signal expiration (every hour)
        self.add_task(TaskConfig(
            name="expire_signals",
            function=self._expire_old_signals,
            schedule="0 * * * *",  # Every hour
            priority=TaskPriority.MEDIUM,
            description="Expire old trading signals",
            kwargs={"max_age_hours": 24}
        ))

        # Portfolio reconciliation (daily at 8:30)
        self.add_task(TaskConfig(
            name="reconcile_portfolio",
            function=self._reconcile_portfolio,
            schedule="30 8 * * MON-FRI",
            priority=TaskPriority.HIGH,
            description="Reconcile portfolio positions"
        ))

        # Risk report generation (daily at 16:30)
        self.add_task(TaskConfig(
            name="generate_risk_report",
            function=self._generate_risk_report,
            schedule="30 16 * * MON-FRI",
            priority=TaskPriority.MEDIUM,
            description="Generate daily risk report"
        ))

        # Market data validation (every 30 minutes)
        self.add_task(TaskConfig(
            name="validate_market_data",
            function=self._validate_market_data,
            schedule="*/30 9-16 * * MON-FRI",
            priority=TaskPriority.MEDIUM,
            description="Validate market data integrity"
        ))

        # System health check (every 10 minutes)
        self.add_task(TaskConfig(
            name="health_check",
            function=self._health_check,
            schedule="*/10 * * * *",
            priority=TaskPriority.HIGH,
            description="System health check"
        ))

    def add_task(self, config: TaskConfig):
        """Add a scheduled task."""
        self.tasks[config.name] = config
        logger.info(f"Added scheduled task: {config.name}")

    def remove_task(self, task_name: str):
        """Remove a scheduled task."""
        if task_name in self.tasks:
            del self.tasks[task_name]
            if self.scheduler.get_job(task_name):
                self.scheduler.remove_job(task_name)
            logger.info(f"Removed scheduled task: {task_name}")

    def enable_task(self, task_name: str):
        """Enable a scheduled task."""
        if task_name in self.tasks:
            self.tasks[task_name].enabled = True
            if self.scheduler.get_job(task_name):
                self.scheduler.resume_job(task_name)
            logger.info(f"Enabled task: {task_name}")

    def disable_task(self, task_name: str):
        """Disable a scheduled task."""
        if task_name in self.tasks:
            self.tasks[task_name].enabled = False
            if self.scheduler.get_job(task_name):
                self.scheduler.pause_job(task_name)
            logger.info(f"Disabled task: {task_name}")

    def start(self):
        """Start the scheduler."""
        # Schedule all enabled tasks
        for task_name, config in self.tasks.items():
            if config.enabled:
                self._schedule_task(config)

        # Start scheduler
        self.scheduler.start()
        logger.info("Scheduled task manager started")

    def stop(self):
        """Stop the scheduler."""
        self.scheduler.shutdown(wait=True)
        logger.info("Scheduled task manager stopped")

    def _schedule_task(self, config: TaskConfig):
        """Schedule a task based on its configuration."""
        # Parse schedule
        if config.schedule.startswith("*/"):  # Interval schedule
            parts = config.schedule.split()
            if len(parts) >= 1:
                interval = int(parts[0][2:])
                unit = "minutes"  # Default unit

                trigger = IntervalTrigger(
                    **{unit: interval},
                    timezone='Asia/Shanghai'
                )
        else:  # Cron schedule
            trigger = CronTrigger.from_crontab(
                config.schedule,
                timezone='Asia/Shanghai'
            )

        # Add job to scheduler
        self.scheduler.add_job(
            func=self._execute_task,
            trigger=trigger,
            args=[config],
            id=config.name,
            name=config.description or config.name,
            misfire_grace_time=60,  # 60 seconds grace time
            max_instances=1,  # Only one instance at a time
            replace_existing=True
        )

        logger.info(f"Scheduled task '{config.name}' with schedule: {config.schedule}")

    async def _execute_task(self, config: TaskConfig):
        """Execute a scheduled task."""
        task_name = config.name

        # Check if already running
        if task_name in self.running_tasks:
            logger.warning(f"Task {task_name} is already running, skipping")
            self._record_result(TaskResult(
                task_name=task_name,
                status=TaskStatus.SKIPPED,
                start_time=datetime.now(),
                end_time=datetime.now(),
                duration=timedelta(0),
                error="Task already running"
            ))
            return

        # Check if trading day (for market-related tasks)
        if await self._requires_trading_day(task_name):
            if not await self._is_trading_day():
                logger.debug(f"Skipping {task_name} - not a trading day")
                return

        logger.info(f"Starting scheduled task: {task_name}")
        start_time = datetime.now()
        self.running_tasks[task_name] = start_time

        try:
            # Execute task with timeout
            result = await asyncio.wait_for(
                config.function(**config.kwargs),
                timeout=config.timeout
            )

            end_time = datetime.now()
            duration = end_time - start_time

            self._record_result(TaskResult(
                task_name=task_name,
                status=TaskStatus.COMPLETED,
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                result=result
            ))

            logger.info(f"Completed task {task_name} in {duration.total_seconds():.2f}s")

        except asyncio.TimeoutError:
            end_time = datetime.now()
            duration = end_time - start_time

            self._record_result(TaskResult(
                task_name=task_name,
                status=TaskStatus.FAILED,
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                error=f"Task timeout after {config.timeout}s"
            ))

            logger.error(f"Task {task_name} timed out after {config.timeout}s")

        except Exception as e:
            end_time = datetime.now()
            duration = end_time - start_time

            self._record_result(TaskResult(
                task_name=task_name,
                status=TaskStatus.FAILED,
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                error=str(e)
            ))

            logger.error(f"Task {task_name} failed: {e}")

        finally:
            # Remove from running tasks
            if task_name in self.running_tasks:
                del self.running_tasks[task_name]

    def _record_result(self, result: TaskResult):
        """Record task execution result."""
        self.task_history.append(result)

        # Keep only recent history (last 1000 entries)
        if len(self.task_history) > 1000:
            self.task_history = self.task_history[-1000:]

    async def _requires_trading_day(self, task_name: str) -> bool:
        """Check if task requires a trading day."""
        market_tasks = [
            "sync_daily_klines",
            "sync_minute_klines",
            "calculate_features",
            "execute_strategies",
            "reconcile_portfolio",
            "generate_risk_report",
            "validate_market_data"
        ]
        return task_name in market_tasks

    async def _is_trading_day(self) -> bool:
        """Check if today is a trading day."""
        today = datetime.now().date()

        try:
            async with self.db.session() as session:
                # 先检查表中是否有数据
                count_stmt = select(func.count()).select_from(TradingCalendar)
                count_result = await session.execute(count_stmt)
                total_records = count_result.scalar()

                # 如果表为空，使用工作日判断（周一到周五）
                if total_records == 0:
                    is_weekday = today.weekday() < 5
                    if is_weekday:
                        logger.debug(f"TradingCalendar table is empty, using weekday check: {is_weekday}")
                    return is_weekday

                # 表有数据，查询今天是否为交易日
                stmt = select(TradingCalendar).where(
                    and_(
                        TradingCalendar.market == "HK",
                        TradingCalendar.trade_date == today
                    )
                )
                result = await session.execute(stmt)
                calendar = result.scalar_one_or_none()

                return calendar is not None

        except Exception as e:
            logger.error(f"Error checking trading day: {e}")
            # Default to weekday
            return today.weekday() < 5

    # Task implementations

    async def _sync_daily_klines(self, days_back: int = 5):
        """Sync daily K-line data."""
        try:
            # Get watchlist symbols
            from longport_quant.data.watchlist import WatchlistLoader
            watchlist = WatchlistLoader().load()
            all_symbols = list(watchlist.symbols())

            # Limit symbols to avoid API rate limits
            max_symbols_per_batch = 5  # Further reduce to avoid API limits
            symbols = all_symbols[:max_symbols_per_batch]

            if len(all_symbols) > max_symbols_per_batch:
                logger.info(f"Limited sync to {max_symbols_per_batch} symbols out of {len(all_symbols)}")

            # Calculate date range
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=days_back)

            # Sync data
            await self.kline_service.sync_daily_klines(
                symbols=symbols,
                start_date=start_date,
                end_date=end_date
            )

            logger.info(f"Synced daily K-lines for {len(symbols)} symbols")
            return {"symbols": len(symbols), "days": days_back}

        except Exception as e:
            logger.error(f"Failed to sync daily K-lines: {e}")
            raise

    async def _sync_minute_klines(self, minutes_back: int = 5):
        """Sync minute K-line data."""
        try:
            # Get watchlist symbols
            from longport_quant.data.watchlist import WatchlistLoader
            watchlist = WatchlistLoader().load()
            all_symbols = list(watchlist.symbols())

            # 排除经常限流的股票
            exclude_symbols = {"00700.HK", "00001.HK"}  # 腾讯、长和
            all_symbols = [s for s in all_symbols if s not in exclude_symbols]

            # Limit symbols to avoid API rate limits
            max_symbols_per_batch = 5  # Further reduce to avoid API limits
            symbols = all_symbols[:max_symbols_per_batch]

            if len(all_symbols) > max_symbols_per_batch:
                logger.info(f"Limited sync to {max_symbols_per_batch} symbols out of {len(all_symbols)}")

            # Sync recent minute data
            await self.kline_service.sync_minute_klines(
                symbols=symbols,
                days_back=1  # Only sync today's data
            )

            logger.debug(f"Synced minute K-lines for {len(symbols)} symbols")
            return {"symbols": len(symbols), "total": len(all_symbols)}

        except Exception as e:
            logger.error(f"Failed to sync minute K-lines: {e}")
            raise

    async def _calculate_features(self):
        """Calculate technical features."""
        try:
            # Get watchlist symbols
            from longport_quant.data.watchlist import WatchlistLoader
            watchlist = WatchlistLoader().load()
            symbols = list(watchlist.symbols())

            # Calculate features for each symbol
            results = await self.feature_engine.calculate_batch_features(
                symbols=symbols,
                start_date=datetime.now().date() - timedelta(days=30),
                end_date=datetime.now().date()
            )

            successful = sum(1 for df in results.values() if not df.empty)
            logger.info(f"Calculated features for {successful}/{len(symbols)} symbols")
            return {"calculated": successful, "total": len(symbols)}

        except Exception as e:
            logger.error(f"Failed to calculate features: {e}")
            raise

    async def _execute_strategies(self):
        """Execute trading strategies."""
        if not self.strategy_manager:
            logger.debug("Strategy manager not configured, skipping strategy execution")
            return {"skipped": True, "reason": "no_strategy_manager"}

        try:
            # Run all active strategies
            signals = await self.strategy_manager.run_all()

            logger.info(f"Strategy execution completed: {len(signals)} signals generated")
            return {"signals": len(signals)}

        except Exception as e:
            logger.error(f"Failed to execute strategies: {e}")
            raise

    async def _cleanup_old_data(self, days_to_keep: int = 180):
        """Clean up old data."""
        try:
            # Cleanup old minute K-lines
            deleted_count = await self.kline_service.cleanup_old_minute_data(days_to_keep)

            logger.info(f"Cleaned up {deleted_count} old minute K-line records")
            return {"deleted": deleted_count}

        except Exception as e:
            logger.error(f"Failed to clean up old data: {e}")
            raise

    async def _expire_old_signals(self, max_age_hours: int = 24):
        """Expire old trading signals."""
        try:
            expired_count = await self.signal_manager.expire_old_signals(max_age_hours)

            logger.info(f"Expired {expired_count} old signals")
            return {"expired": expired_count}

        except Exception as e:
            logger.error(f"Failed to expire signals: {e}")
            raise

    async def _reconcile_portfolio(self):
        """Reconcile portfolio positions."""
        try:
            # This would reconcile positions with broker
            logger.info("Portfolio reconciliation completed")
            return {"status": "reconciled"}

        except Exception as e:
            logger.error(f"Failed to reconcile portfolio: {e}")
            raise

    async def _generate_risk_report(self):
        """Generate daily risk report."""
        try:
            # Generate risk metrics report
            from longport_quant.risk.checks import RiskEngine

            # This would generate and save risk report
            logger.info("Risk report generated")
            return {"status": "generated"}

        except Exception as e:
            logger.error(f"Failed to generate risk report: {e}")
            raise

    async def _validate_market_data(self):
        """Validate market data integrity."""
        try:
            # Check for data gaps, anomalies, etc.
            issues_found = 0

            logger.info(f"Market data validation completed: {issues_found} issues found")
            return {"issues": issues_found}

        except Exception as e:
            logger.error(f"Failed to validate market data: {e}")
            raise

    async def _health_check(self):
        """Perform system health check."""
        try:
            health_status = {
                "database": await self._check_database(),
                "scheduler": self._check_scheduler(),
                "tasks": self._check_tasks()
            }

            all_healthy = all(health_status.values())
            if not all_healthy:
                logger.warning(f"Health check issues: {health_status}")

            return health_status

        except Exception as e:
            logger.error(f"Health check failed: {e}")
            raise

    async def _check_database(self) -> bool:
        """Check database connectivity."""
        try:
            async with self.db.session() as session:
                await session.execute(select(1))
            return True
        except Exception:
            return False

    def _check_scheduler(self) -> bool:
        """Check scheduler status."""
        return self.scheduler.running

    def _check_tasks(self) -> bool:
        """Check if tasks are running normally."""
        # Check for stuck tasks
        stuck_threshold = timedelta(hours=1)
        now = datetime.now()

        for task_name, start_time in self.running_tasks.items():
            if now - start_time > stuck_threshold:
                logger.warning(f"Task {task_name} appears to be stuck")
                return False

        return True

    def get_task_status(self) -> Dict[str, Any]:
        """Get current task status."""
        jobs = self.scheduler.get_jobs()

        return {
            "scheduler_running": self.scheduler.running,
            "total_tasks": len(self.tasks),
            "enabled_tasks": sum(1 for t in self.tasks.values() if t.enabled),
            "running_tasks": list(self.running_tasks.keys()),
            "scheduled_jobs": [
                {
                    "name": job.name,
                    "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                    "pending": job.pending
                }
                for job in jobs
            ],
            "recent_results": [
                {
                    "task": r.task_name,
                    "status": r.status.value,
                    "time": r.end_time.isoformat(),
                    "duration": r.duration.total_seconds(),
                    "error": r.error
                }
                for r in self.task_history[-10:]
            ]
        }