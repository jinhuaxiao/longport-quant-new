#!/usr/bin/env python3
"""Run the scheduled task manager."""

import asyncio
import argparse
import signal
import sys
import os
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.data.kline_sync import KlineDataService
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.features.feature_engine import FeatureEngine
from longport_quant.signals.signal_manager import SignalManager
from longport_quant.scheduler.tasks import ScheduledTaskManager, TaskPriority
from longport_quant.config import Settings, get_settings
from longport_quant.config.sdk import build_sdk_config
from longport.openapi import QuoteContext, TradeContext


class SchedulerRunner:
    """Runner for scheduled tasks."""

    def __init__(self):
        """Initialize scheduler runner."""
        self.settings = get_settings()
        self.db = DatabaseSessionManager(self.settings.database_dsn, auto_init=True)
        self.task_manager = None
        self.running = False

    async def setup(self):
        """Setup services and task manager."""
        logger.info("Setting up scheduler services...")

        # Initialize LongPort contexts
        sdk_config = build_sdk_config(self.settings)
        quote_context = QuoteContext(sdk_config)
        trade_context = TradeContext(sdk_config)

        # Initialize services
        quote_client = QuoteDataClient(self.settings)
        kline_service = KlineDataService(self.settings, self.db, quote_client)
        feature_engine = FeatureEngine(self.db)
        signal_manager = SignalManager(self.db)

        # Initialize task manager
        self.task_manager = ScheduledTaskManager(
            db=self.db,
            kline_service=kline_service,
            feature_engine=feature_engine,
            signal_manager=signal_manager
        )

        logger.info("Scheduler services initialized")

    async def start(self):
        """Start the scheduler."""
        self.running = True
        logger.info("Starting scheduled task manager...")

        # Start task manager
        self.task_manager.start()

        # Keep running
        try:
            while self.running:
                await asyncio.sleep(60)  # Check every minute

                # Log status periodically
                status = self.task_manager.get_task_status()
                logger.debug(f"Scheduler status: {status['enabled_tasks']} tasks enabled, "
                           f"{len(status['running_tasks'])} running")

        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        finally:
            await self.stop()

    async def stop(self):
        """Stop the scheduler."""
        logger.info("Stopping scheduled task manager...")
        self.running = False

        if self.task_manager:
            self.task_manager.stop()

        if self.db:
            await self.db.close()
        logger.info("Scheduler stopped")

    async def run_once(self, task_name: str):
        """Run a specific task once."""
        if not self.task_manager:
            await self.setup()

        logger.info(f"Running task: {task_name}")

        if task_name not in self.task_manager.tasks:
            logger.error(f"Task not found: {task_name}")
            return

        config = self.task_manager.tasks[task_name]
        await self.task_manager._execute_task(config)

    def list_tasks(self):
        """List all available tasks."""
        print("\nAvailable Tasks:")
        print("-" * 80)

        for name, config in self.task_manager.tasks.items():
            status = "Enabled" if config.enabled else "Disabled"
            priority = config.priority.name
            print(f"{name:25} | {status:8} | {priority:8} | {config.description or 'No description'}")

        print("-" * 80)

    def enable_task(self, task_name: str):
        """Enable a task."""
        self.task_manager.enable_task(task_name)
        logger.info(f"Task '{task_name}' enabled")

    def disable_task(self, task_name: str):
        """Disable a task."""
        self.task_manager.disable_task(task_name)
        logger.info(f"Task '{task_name}' disabled")

    def get_status(self):
        """Get scheduler status."""
        status = self.task_manager.get_task_status()

        print("\nScheduler Status")
        print("-" * 80)
        print(f"Scheduler Running: {status['scheduler_running']}")
        print(f"Total Tasks: {status['total_tasks']}")
        print(f"Enabled Tasks: {status['enabled_tasks']}")
        print(f"Running Tasks: {len(status['running_tasks'])}")

        if status['running_tasks']:
            print(f"\nCurrently Running:")
            for task in status['running_tasks']:
                print(f"  - {task}")

        print(f"\nNext Scheduled Jobs:")
        for job in status['scheduled_jobs'][:5]:
            if job['next_run']:
                print(f"  - {job['name']:25} at {job['next_run']}")

        if status['recent_results']:
            print(f"\nRecent Task Results:")
            for result in status['recent_results']:
                status_emoji = "✓" if result['status'] == "completed" else "✗"
                print(f"  {status_emoji} {result['task']:20} | {result['status']:10} | "
                      f"{result['duration']:.2f}s | {result['time']}")
                if result['error']:
                    print(f"    Error: {result['error']}")

        print("-" * 80)


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Scheduled Task Manager')
    parser.add_argument(
        '--mode',
        choices=['run', 'once', 'list', 'status', 'enable', 'disable'],
        default='run',
        help='Operation mode'
    )
    parser.add_argument(
        '--task',
        type=str,
        help='Task name (for once/enable/disable modes)'
    )
    parser.add_argument(
        '--enable-trading',
        action='store_true',
        help='Enable trading strategy execution'
    )

    args = parser.parse_args()

    # Setup logging
    logger.add(
        f"scheduler_{datetime.now().strftime('%Y%m%d')}.log",
        rotation="1 day",
        retention="7 days",
        level="DEBUG"
    )

    # Create runner
    runner = SchedulerRunner()

    # Setup
    await runner.setup()

    # Enable trading if requested
    if args.enable_trading:
        runner.enable_task("execute_strategies")
        logger.warning("Trading strategy execution enabled - USE WITH CAUTION")

    # Execute based on mode
    if args.mode == 'run':
        # Run scheduler continuously
        await runner.start()

    elif args.mode == 'once':
        # Run specific task once
        if not args.task:
            logger.error("Task name required for 'once' mode")
            sys.exit(1)
        await runner.run_once(args.task)

    elif args.mode == 'list':
        # List all tasks
        runner.list_tasks()

    elif args.mode == 'status':
        # Show status
        runner.get_status()

    elif args.mode == 'enable':
        # Enable task
        if not args.task:
            logger.error("Task name required for 'enable' mode")
            sys.exit(1)
        runner.enable_task(args.task)

    elif args.mode == 'disable':
        # Disable task
        if not args.task:
            logger.error("Task name required for 'disable' mode")
            sys.exit(1)
        runner.disable_task(args.task)


if __name__ == '__main__':
    # Handle signals
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run main
    asyncio.run(main())