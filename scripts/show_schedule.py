#!/usr/bin/env python3
"""显示调度任务的执行时间表"""

import asyncio
from datetime import datetime, timedelta
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from longport_quant.config import get_settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.data.kline_sync import KlineDataService
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.features.feature_engine import FeatureEngine
from longport_quant.signals.signal_manager import SignalManager
from longport_quant.scheduler.tasks import ScheduledTaskManager


async def main():
    """显示任务调度时间表"""
    settings = get_settings()
    db = DatabaseSessionManager(settings.database_dsn, auto_init=True)

    # 初始化服务
    quote_client = QuoteDataClient(settings)
    kline_service = KlineDataService(settings, db, quote_client)
    feature_engine = FeatureEngine(db)
    signal_manager = SignalManager(db)

    # 初始化任务管理器
    task_manager = ScheduledTaskManager(
        db=db,
        kline_service=kline_service,
        feature_engine=feature_engine,
        signal_manager=signal_manager
    )

    print("\n" + "=" * 80)
    print("量化交易系统 - 自动化任务时间表")
    print("=" * 80)
    print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")
    print()

    # 显示所有任务
    print("任务列表及执行时间:")
    print("-" * 80)
    print(f"{'任务名称':<25} {'状态':<10} {'优先级':<10} {'调度时间':<30}")
    print("-" * 80)

    for task_name, config in task_manager.tasks.items():
        status = "启用" if config.enabled else "禁用"
        priority = config.priority.name
        schedule = config.schedule

        # 解析下次执行时间
        if config.schedule.startswith("*/"):  # 间隔调度
            parts = config.schedule.split()
            interval = parts[0][2:]
            time_range = parts[1] if len(parts) > 1 else "全天"
            next_run = f"每{interval}分钟 ({time_range}时)"
        else:  # Cron调度
            next_run = config.schedule

        print(f"{task_name:<25} {status:<10} {priority:<10} {next_run:<30}")

    print()
    print("任务说明:")
    print("-" * 80)
    for task_name, config in task_manager.tasks.items():
        if config.description:
            print(f"  • {task_name}: {config.description}")

    # 交易时段说明
    print()
    print("交易时段:")
    print("-" * 80)
    print("  港股 (HK):")
    print("    - 开盘前: 09:00-09:30")
    print("    - 早市: 09:30-12:00")
    print("    - 午市: 13:00-16:00")
    print("    - 收盘后: 16:00-17:00")
    print()
    print("  美股 (US) - 北京时间:")
    print("    - 夏令时: 21:30-04:00")
    print("    - 冬令时: 22:30-05:00")

    # 自动化执行说明
    print()
    print("自动化执行说明:")
    print("-" * 80)
    print("  1. sync_minute_klines: 交易时间每分钟同步最新K线数据")
    print("  2. execute_strategies: 交易时间每分钟执行策略，生成交易信号")
    print("  3. calculate_features: 每5分钟计算技术指标")
    print("  4. sync_daily_klines: 每天17:30同步日线数据")
    print("  5. health_check: 每10分钟进行系统健康检查")

    # 当前启用的策略
    if config.enabled and task_name == "execute_strategies":
        print()
        print("⚠️  注意: 策略执行已启用，系统将自动生成交易信号!")

    await db.close()

    print()
    print("=" * 80)
    print("提示: 使用 'python scripts/run_scheduler.py --mode run' 启动自动交易")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())