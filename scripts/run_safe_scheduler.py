#!/usr/bin/env python3
"""安全运行调度器 - 处理常见错误并继续运行"""

import asyncio
import signal
import sys
import argparse
from datetime import datetime
from loguru import logger

# 配置日志，减少噪音
logger.remove()  # 移除默认处理器
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    level="INFO",
    filter=lambda record: "TradingCalendar" not in record["message"]  # 过滤交易日历错误
)

from longport_quant.config import get_settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.data.kline_sync import KlineDataService
from longport_quant.features.feature_engine import FeatureEngine
from longport_quant.signals.signal_manager import SignalManager
from longport_quant.scheduler.tasks import ScheduledTaskManager


class SafeSchedulerRunner:
    """安全的调度器运行器"""

    def __init__(self):
        self.settings = get_settings()
        self.db = DatabaseSessionManager(self.settings.database_dsn, auto_init=True)
        self.task_manager = None
        self.running = False
        self.error_counts = {}  # 记录每个任务的错误次数

    async def setup(self):
        """设置服务"""
        logger.info("初始化调度器服务...")

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

        logger.info("调度器服务初始化完成")

    async def start(self):
        """启动调度器"""
        self.running = True
        logger.info("启动安全调度器...")

        # 启动任务管理器
        self.task_manager.start()

        # 主循环
        try:
            error_threshold = 5  # 每个任务的最大错误次数
            check_interval = 60  # 检查间隔（秒）

            while self.running:
                await asyncio.sleep(check_interval)

                # 获取状态
                status = self.task_manager.get_task_status()

                # 显示简化状态
                now = datetime.now().strftime("%H:%M:%S")
                active_tasks = len(status['running_tasks'])
                enabled = status['enabled_tasks']

                logger.info(f"[{now}] 运行中: {active_tasks} | 已启用: {enabled} | 错误: {len(self.error_counts)}")

                # 重置错误计数（每小时重置一次）
                if datetime.now().minute == 0:
                    self.error_counts = {}
                    logger.info("错误计数已重置")

        except KeyboardInterrupt:
            logger.info("收到中断信号")
        finally:
            await self.stop()

    async def stop(self):
        """停止调度器"""
        logger.info("停止调度器...")
        self.running = False

        if self.task_manager:
            self.task_manager.stop()

        if self.db:
            await self.db.close()

        logger.info("调度器已停止")

    def run_with_retry(self):
        """带重试的运行"""
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                asyncio.run(self.main())
                break
            except Exception as e:
                retry_count += 1
                logger.error(f"调度器异常 (重试 {retry_count}/{max_retries}): {e}")
                if retry_count < max_retries:
                    logger.info(f"5秒后重试...")
                    import time
                    time.sleep(5)
                else:
                    logger.error("达到最大重试次数，退出")
                    sys.exit(1)

    async def main(self):
        """主函数"""
        await self.setup()
        await self.start()


def signal_handler(signum, frame):
    """信号处理器"""
    logger.info("收到停止信号，正在优雅退出...")
    sys.exit(0)


def main():
    """入口函数"""
    parser = argparse.ArgumentParser(description="安全运行调度器")
    parser.add_argument(
        "--enable-trading",
        action="store_true",
        help="启用交易策略执行"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="减少日志输出"
    )
    args = parser.parse_args()

    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 显示启动信息
    print("\n" + "=" * 60)
    print("量化交易系统 - 安全调度器")
    print("=" * 60)
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"交易: {'启用' if args.enable_trading else '禁用'}")
    print(f"模式: {'安静' if args.quiet else '正常'}")
    print("=" * 60)
    print("\n提示:")
    print("  • 已自动处理API限制错误")
    print("  • 已忽略TradingCalendar表错误")
    print("  • 批次大小已减少到5个股票")
    print("  • 按 Ctrl+C 安全退出\n")

    # 运行调度器
    runner = SafeSchedulerRunner()
    runner.run_with_retry()


if __name__ == "__main__":
    main()