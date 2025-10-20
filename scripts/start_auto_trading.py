#!/usr/bin/env python3
"""启动自动交易系统 - 根据市场时间自动执行策略"""

import asyncio
import signal
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from loguru import logger

from longport_quant.config import get_settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.data.kline_sync import KlineDataService
from longport_quant.features.feature_engine import FeatureEngine
from longport_quant.signals.signal_manager import SignalManager
from longport_quant.scheduler.tasks import ScheduledTaskManager


class AutoTradingSystem:
    """自动交易系统"""

    def __init__(self):
        self.settings = get_settings()
        self.db = DatabaseSessionManager(self.settings.database_dsn, auto_init=True)
        self.task_manager = None
        self.running = False
        self.beijing_tz = ZoneInfo('Asia/Shanghai')

    def get_market_status(self):
        """获取当前市场状态"""
        now = datetime.now(self.beijing_tz)
        weekday = now.weekday()  # 0=Monday, 6=Sunday

        # 周末休市
        if weekday >= 5:
            return {
                'is_trading_day': False,
                'current_time': now.strftime('%Y-%m-%d %H:%M:%S'),
                'reason': '周末休市'
            }

        hour = now.hour
        minute = now.minute
        current_minutes = hour * 60 + minute

        status = {
            'is_trading_day': True,
            'current_time': now.strftime('%Y-%m-%d %H:%M:%S'),
            'hk_market': 'closed',
            'us_market': 'closed'
        }

        # 港股交易时间 (9:30-12:00, 13:00-16:00)
        if 570 <= current_minutes < 720:  # 9:30-12:00
            status['hk_market'] = 'morning'
        elif 780 <= current_minutes < 960:  # 13:00-16:00
            status['hk_market'] = 'afternoon'
        elif 540 <= current_minutes < 570:  # 9:00-9:30
            status['hk_market'] = 'pre_open'

        # 美股交易时间 (北京时间 21:30-04:00 夏令时)
        if current_minutes >= 1290 or current_minutes < 240:  # 21:30-04:00
            status['us_market'] = 'trading'
        elif 990 <= current_minutes < 1290:  # 16:30-21:30
            status['us_market'] = 'pre_market'

        return status

    async def setup(self):
        """设置服务"""
        logger.info("初始化自动交易系统...")

        # 初始化服务
        quote_client = QuoteDataClient(self.settings)
        kline_service = KlineDataService(self.settings, self.db, quote_client)
        feature_engine = FeatureEngine(self.db)
        signal_manager = SignalManager(self.db)

        # 初始化任务管理器
        self.task_manager = ScheduledTaskManager(
            db=self.db,
            kline_service=kline_service,
            feature_engine=feature_engine,
            signal_manager=signal_manager
        )

        # 根据市场状态调整任务
        self.adjust_tasks_for_market()

        logger.info("自动交易系统初始化完成")

    def adjust_tasks_for_market(self):
        """根据市场状态调整任务"""
        status = self.get_market_status()

        if not status['is_trading_day']:
            logger.info(f"非交易日: {status.get('reason', '')}")
            # 禁用交易相关任务
            self.task_manager.disable_task('sync_minute_klines')
            self.task_manager.disable_task('execute_strategies')
            return

        # 根据市场时间启用相应任务
        if status['hk_market'] != 'closed':
            logger.info(f"港股市场: {status['hk_market']}")
            self.task_manager.enable_task('sync_minute_klines')
            self.task_manager.enable_task('execute_strategies')

        if status['us_market'] != 'closed':
            logger.info(f"美股市场: {status['us_market']}")
            # 美股任务可以单独配置

    async def monitor_market_changes(self):
        """监控市场变化"""
        last_status = None

        while self.running:
            current_status = self.get_market_status()

            # 检测市场状态变化
            if last_status != current_status:
                logger.info(f"市场状态变化: HK={current_status['hk_market']}, US={current_status['us_market']}")
                self.adjust_tasks_for_market()
                last_status = current_status

            # 每分钟检查一次
            await asyncio.sleep(60)

    async def start(self):
        """启动系统"""
        self.running = True
        logger.info("启动自动交易系统...")

        # 启动任务管理器
        self.task_manager.start()

        # 启动市场监控
        monitor_task = asyncio.create_task(self.monitor_market_changes())

        # 显示状态
        try:
            while self.running:
                status = self.get_market_status()
                task_status = self.task_manager.get_task_status()

                logger.info(f"系统运行中 | 时间: {status['current_time']} | "
                          f"港股: {status['hk_market']} | 美股: {status['us_market']} | "
                          f"任务: {task_status['enabled_tasks']} 启用, {len(task_status['running_tasks'])} 运行中")

                # 每5分钟输出一次状态
                await asyncio.sleep(300)

        except KeyboardInterrupt:
            logger.info("收到中断信号")
        finally:
            await self.stop()
            monitor_task.cancel()

    async def stop(self):
        """停止系统"""
        logger.info("停止自动交易系统...")
        self.running = False

        if self.task_manager:
            self.task_manager.stop()

        if self.db:
            await self.db.close()

        logger.info("自动交易系统已停止")


async def main():
    """主函数"""
    system = AutoTradingSystem()

    # 显示启动信息
    print("\n" + "=" * 80)
    print("量化交易系统 - 自动交易模式")
    print("=" * 80)

    status = system.get_market_status()
    print(f"当前时间: {status['current_time']}")
    print(f"交易日: {'是' if status['is_trading_day'] else '否'}")
    print(f"港股市场: {status['hk_market']}")
    print(f"美股市场: {status['us_market']}")
    print("=" * 80)

    if not status['is_trading_day']:
        print(f"⚠️ {status.get('reason', '非交易日')}")
        print("系统将在待机模式下运行，等待下一个交易日")
    else:
        if status['hk_market'] != 'closed':
            print(f"✅ 港股交易中: {status['hk_market']}")
        if status['us_market'] != 'closed':
            print(f"✅ 美股交易中: {status['us_market']}")

    print("\n按 Ctrl+C 停止系统")
    print("=" * 80 + "\n")

    # 设置信号处理
    def signal_handler(signum, frame):
        logger.info("收到停止信号")
        asyncio.create_task(system.stop())

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 启动系统
    await system.setup()
    await system.start()


if __name__ == "__main__":
    asyncio.run(main())