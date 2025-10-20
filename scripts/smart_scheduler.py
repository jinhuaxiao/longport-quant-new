#!/usr/bin/env python3
"""智能调度器 - 根据时间和市场状态智能执行任务"""

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from loguru import logger
import yaml


class SmartScheduler:
    """智能调度器"""

    def __init__(self):
        self.beijing_tz = ZoneInfo('Asia/Shanghai')
        self.load_api_limits()

    def load_api_limits(self):
        """加载API限制配置"""
        try:
            with open('configs/api_limits.yml', 'r') as f:
                self.api_config = yaml.safe_load(f)
        except:
            self.api_config = {
                'sync_limits': {
                    'minute_klines': {'max_symbols_per_batch': 3},
                    'daily_klines': {'max_symbols_per_batch': 5}
                },
                'exclude_symbols': ['00001.HK', '00700.HK']
            }

    def get_current_task(self):
        """根据当前时间确定应该执行的任务"""
        now = datetime.now(self.beijing_tz)
        hour = now.hour
        minute = now.minute
        weekday = now.weekday()

        # 周末
        if weekday >= 5:
            return {
                'primary': 'maintenance',
                'tasks': ['cleanup_old_data', 'validate_market_data'],
                'reason': '周末维护'
            }

        # 交易日
        tasks = []

        # 港股交易时间
        if 9 <= hour <= 16:
            if (9 <= hour < 12) or (13 <= hour < 16):
                tasks.extend(['sync_minute_klines', 'execute_strategies'])
                if minute % 5 == 0:  # 每5分钟
                    tasks.append('calculate_features')

        # 港股收盘后
        if hour == 17 and minute >= 30:
            tasks.append('sync_daily_klines')
            tasks.append('generate_risk_report')

        # 美股盘前
        if (hour == 16 and minute >= 30) or (17 <= hour < 21):
            tasks.append('prepare_us_trading')

        # 美股交易
        if hour >= 21 or hour < 4:
            tasks.extend(['sync_us_data', 'execute_us_strategies'])

        # 每小时任务
        if minute == 0:
            tasks.append('expire_signals')

        # 每10分钟
        if minute % 10 == 0:
            tasks.append('health_check')

        return {
            'primary': 'trading' if tasks else 'idle',
            'tasks': tasks,
            'reason': f'{hour:02d}:{minute:02d} 交易日'
        }

    async def execute_task_safe(self, task_name):
        """安全执行单个任务"""
        import subprocess

        logger.info(f"执行任务: {task_name}")

        try:
            # 使用子进程执行，避免错误影响主进程
            result = subprocess.run(
                ['python', 'scripts/run_scheduler.py', '--mode', 'once', '--task', task_name],
                capture_output=True,
                text=True,
                timeout=60  # 60秒超时
            )

            if result.returncode == 0:
                logger.success(f"✅ {task_name} 执行成功")
            else:
                logger.error(f"❌ {task_name} 执行失败")

        except subprocess.TimeoutExpired:
            logger.warning(f"⏱️ {task_name} 执行超时")
        except Exception as e:
            logger.error(f"❌ {task_name} 执行异常: {e}")

    async def run_once(self):
        """运行一次"""
        current = self.get_current_task()

        logger.info(f"当前状态: {current['reason']}")
        logger.info(f"待执行任务: {', '.join(current['tasks']) if current['tasks'] else '无'}")

        # 执行任务
        for task in current['tasks']:
            if task in ['sync_minute_klines', 'sync_daily_klines']:
                # 数据同步任务
                await self.execute_task_safe(task)
                await asyncio.sleep(2)  # API限制延迟

            elif task == 'execute_strategies':
                # 策略执行
                logger.info("策略执行已启用，生成交易信号")
                await self.execute_task_safe(task)

            elif task == 'health_check':
                # 健康检查
                logger.debug("系统健康检查")

            else:
                logger.debug(f"跳过任务: {task}")

    async def run_continuous(self):
        """持续运行"""
        logger.info("智能调度器启动，持续监控模式")

        while True:
            try:
                await self.run_once()

                # 智能等待
                now = datetime.now(self.beijing_tz)
                if 9 <= now.hour <= 16:  # 交易时间
                    wait_seconds = 60  # 每分钟检查
                else:
                    wait_seconds = 300  # 非交易时间每5分钟

                logger.info(f"等待 {wait_seconds} 秒...")
                await asyncio.sleep(wait_seconds)

            except KeyboardInterrupt:
                logger.info("收到中断信号，退出")
                break
            except Exception as e:
                logger.error(f"运行异常: {e}")
                await asyncio.sleep(10)


async def main():
    """主函数"""
    scheduler = SmartScheduler()

    print("\n" + "=" * 80)
    print("智能调度器 - 自动执行最适合的任务")
    print("=" * 80)

    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    print(f"当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')} 北京时间")

    current = scheduler.get_current_task()
    print(f"当前模式: {current['primary']}")
    print(f"计划任务: {', '.join(current['tasks']) if current['tasks'] else '无'}")
    print("=" * 80)

    print("\n选择运行模式:")
    print("1. 运行一次（推荐测试）")
    print("2. 持续运行")
    print("3. 查看当前应执行的任务")

    try:
        choice = input("\n请选择 (1/2/3): ").strip()
    except KeyboardInterrupt:
        print("\n退出")
        return

    if choice == '1':
        await scheduler.run_once()
    elif choice == '2':
        await scheduler.run_continuous()
    elif choice == '3':
        print(f"\n当前应执行: {current['tasks']}")
    else:
        print("无效选择")


if __name__ == "__main__":
    asyncio.run(main())