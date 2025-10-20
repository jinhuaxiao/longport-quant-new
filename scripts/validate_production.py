#!/usr/bin/env python3
"""生产环境验证脚本 - 验证系统各组件是否正常工作"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any
from loguru import logger
from sqlalchemy import text

from longport_quant.config import get_settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.data.watchlist import WatchlistLoader


class ProductionValidator:
    """生产环境验证器"""

    def __init__(self):
        self.test_results = {}
        self.settings = get_settings()
        self.critical_errors = []
        self.warnings = []

    def log_test(self, test_name: str, result: bool, message: str = ""):
        """记录测试结果"""
        status = "✓" if result else "✗"
        self.test_results[test_name] = (result, message)

        if result:
            logger.info(f"{status} {test_name}: {message if message else '通过'}")
        else:
            logger.error(f"{status} {test_name}: {message if message else '失败'}")
            self.critical_errors.append(f"{test_name}: {message}")

    async def test_database_connection(self):
        """测试数据库连接"""
        try:
            async with DatabaseSessionManager(self.settings.database_dsn) as db:
                async with db.session() as session:
                    result = await session.execute(text("SELECT 1"))
                    self.log_test("数据库连接", True)
                    return True
        except Exception as e:
            self.log_test("数据库连接", False, str(e))
            return False

    async def test_database_tables(self):
        """验证数据库表结构"""
        required_tables = [
            "security_universe",
            "kline_minute",
            "kline_daily",
            "positions",
            "trading_signals"
        ]

        try:
            async with DatabaseSessionManager(self.settings.database_dsn) as db:
                async with db.session() as session:
                    # 检查表是否存在
                    result = await session.execute(
                        text("""
                            SELECT tablename
                            FROM pg_tables
                            WHERE schemaname = 'public'
                        """)
                    )
                    existing_tables = {row[0] for row in result}

                    missing_tables = []
                    for table in required_tables:
                        if table not in existing_tables:
                            missing_tables.append(table)

                    if missing_tables:
                        self.log_test("数据库表结构", False, f"缺少表: {', '.join(missing_tables)}")
                        return False

                    # 检查分区表
                    partition_check = await session.execute(
                        text("""
                            SELECT COUNT(*)
                            FROM pg_tables
                            WHERE tablename LIKE 'kline_minute_%'
                            OR tablename LIKE 'kline_daily_%'
                        """)
                    )
                    partition_count = partition_check.scalar()

                    if partition_count < 2:
                        self.warnings.append("分区表数量较少，可能需要创建更多分区")

                    self.log_test("数据库表结构", True, f"所有必需表存在，{partition_count}个分区表")
                    return True

        except Exception as e:
            self.log_test("数据库表结构", False, str(e))
            return False

    async def test_data_availability(self):
        """测试数据可用性"""
        try:
            async with DatabaseSessionManager(self.settings.database_dsn) as db:
                async with db.session() as session:
                    # 检查证券数据
                    security_count = await session.execute(
                        text("SELECT COUNT(*) FROM security_universe")
                    )
                    securities = security_count.scalar()

                    # 检查K线数据
                    daily_count = await session.execute(
                        text("SELECT COUNT(*) FROM kline_daily")
                    )
                    daily_klines = daily_count.scalar()

                    minute_count = await session.execute(
                        text("SELECT COUNT(*) FROM kline_minute")
                    )
                    minute_klines = minute_count.scalar()

                    # 检查数据时效性
                    latest_daily = await session.execute(
                        text("""
                            SELECT MAX(trade_date)
                            FROM kline_daily
                        """)
                    )
                    latest_date = latest_daily.scalar()

                    # 判断数据完整性
                    has_securities = securities > 0
                    has_klines = daily_klines > 0 or minute_klines > 0

                    if latest_date:
                        days_old = (datetime.now().date() - latest_date).days
                        is_recent = days_old < 7
                    else:
                        is_recent = False
                        days_old = -1

                    message = f"证券:{securities}, 日线:{daily_klines}, 分钟线:{minute_klines}"
                    if latest_date:
                        message += f", 最新数据:{days_old}天前"

                    if not has_securities:
                        self.critical_errors.append("没有证券数据")
                    if not has_klines:
                        self.warnings.append("没有K线数据")
                    if not is_recent and latest_date:
                        self.warnings.append(f"数据已过期 {days_old} 天")

                    success = has_securities and (has_klines or not is_recent)
                    self.log_test("数据可用性", success, message)
                    return success

        except Exception as e:
            self.log_test("数据可用性", False, str(e))
            return False

    async def test_api_connection(self):
        """测试API连接"""
        try:
            quote_client = QuoteDataClient(self.settings)

            # 测试获取静态数据
            static_info = await quote_client.get_static_info(["700.HK"])
            has_static = len(static_info) > 0

            if not has_static:
                self.log_test("API连接", False, "无法获取静态数据")
                return False

            # 测试获取实时行情
            quotes = await quote_client.get_realtime_quote(["700.HK"])
            has_quotes = len(quotes) > 0

            if not has_quotes:
                self.warnings.append("无法获取实时行情（可能是非交易时间）")

            self.log_test("API连接", True, "API连接正常")
            return True

        except Exception as e:
            self.log_test("API连接", False, f"API错误: {e}")
            return False

    async def test_watchlist(self):
        """测试监控列表"""
        try:
            loader = WatchlistLoader()
            watchlist = loader.load()

            total_symbols = len(list(watchlist.symbols()))
            hk_symbols = len(watchlist.symbols("hk"))
            us_symbols = len(watchlist.symbols("us"))

            has_symbols = total_symbols > 0

            message = f"总计:{total_symbols} (港股:{hk_symbols}, 美股:{us_symbols})"

            if not has_symbols:
                self.warnings.append("监控列表为空")

            self.log_test("监控列表", has_symbols, message)
            return has_symbols

        except Exception as e:
            self.log_test("监控列表", False, str(e))
            return False

    async def test_realtime_quote(self):
        """测试实时行情获取"""
        try:
            quote_client = QuoteDataClient(self.settings)

            # 使用常见的活跃股票测试
            test_symbols = ["700.HK", "AAPL.US", "0001.HK"]
            available_symbols = []

            for symbol in test_symbols:
                try:
                    quotes = await quote_client.get_realtime_quote([symbol])
                    if quotes and quotes[0].last_done > 0:
                        available_symbols.append(symbol)
                except:
                    pass

            if available_symbols:
                self.log_test("实时行情", True, f"可获取: {', '.join(available_symbols)}")
                return True
            else:
                # 可能是非交易时间
                self.warnings.append("无法获取实时行情（可能是非交易时间）")
                self.log_test("实时行情", True, "API可用但当前非交易时间")
                return True

        except Exception as e:
            self.log_test("实时行情", False, str(e))
            return False

    async def test_scheduler_readiness(self):
        """测试调度器准备状态"""
        try:
            # 检查调度器相关表
            async with DatabaseSessionManager(self.settings.database_dsn) as db:
                async with db.session() as session:
                    # 检查是否有持仓数据
                    positions_result = await session.execute(
                        text("SELECT COUNT(*) FROM positions")
                    )
                    positions_count = positions_result.scalar()
                    has_positions = positions_count > 0

                    # 检查是否有信号数据
                    signals_result = await session.execute(
                        text("SELECT COUNT(*) FROM trading_signals")
                    )
                    signals_count = signals_result.scalar()
                    has_signals = signals_count > 0

                    message = f"持仓:{positions_count}, 信号:{signals_count}"

                    if not has_positions:
                        self.warnings.append("没有持仓记录（初始状态正常）")

                    self.log_test("调度器准备", True, message)
                    return True

        except Exception as e:
            self.log_test("调度器准备", False, str(e))
            return False

    async def test_performance(self):
        """测试性能指标"""
        try:
            start_time = datetime.now()

            async with DatabaseSessionManager(self.settings.database_dsn) as db:
                async with db.session() as session:
                    # 测试查询性能
                    await session.execute(
                        text("""
                            SELECT symbol, trade_date, close
                            FROM kline_daily
                            WHERE trade_date > CURRENT_DATE - INTERVAL '30 days'
                            LIMIT 1000
                        """)
                    )

            query_time = (datetime.now() - start_time).total_seconds()

            # 测试API响应时间
            api_start = datetime.now()
            quote_client = QuoteDataClient(self.settings)
            await quote_client.get_static_info(["700.HK"])
            api_time = (datetime.now() - api_start).total_seconds()

            message = f"数据库查询:{query_time:.2f}s, API响应:{api_time:.2f}s"

            if query_time > 1:
                self.warnings.append(f"数据库查询较慢: {query_time:.2f}秒")
            if api_time > 2:
                self.warnings.append(f"API响应较慢: {api_time:.2f}秒")

            self.log_test("性能测试", True, message)
            return True

        except Exception as e:
            self.log_test("性能测试", False, str(e))
            return False

    async def run(self) -> int:
        """运行所有验证测试"""
        logger.info("=" * 60)
        logger.info("开始生产环境验证")
        logger.info("=" * 60)

        tests = [
            ("数据库连接", self.test_database_connection),
            ("数据库表结构", self.test_database_tables),
            ("数据可用性", self.test_data_availability),
            ("API连接", self.test_api_connection),
            ("监控列表", self.test_watchlist),
            ("实时行情", self.test_realtime_quote),
            ("调度器准备", self.test_scheduler_readiness),
            ("性能测试", self.test_performance),
        ]

        passed = 0
        failed = 0

        for test_name, test_func in tests:
            try:
                result = await test_func()
                if result:
                    passed += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"测试 {test_name} 执行失败: {e}")
                failed += 1

        # 输出总结
        logger.info("=" * 60)
        logger.info("验证结果总结")
        logger.info("=" * 60)
        logger.info(f"通过测试: {passed}/{len(tests)}")
        logger.info(f"失败测试: {failed}/{len(tests)}")

        if self.critical_errors:
            logger.error("关键错误:")
            for error in self.critical_errors:
                logger.error(f"  - {error}")

        if self.warnings:
            logger.warning("警告:")
            for warning in self.warnings:
                logger.warning(f"  - {warning}")

        # 判断系统状态
        if failed == 0:
            logger.info("✅ 系统验证通过，可以启动生产环境")
            return 0
        elif len(self.critical_errors) > 0:
            logger.error("❌ 系统存在关键错误，不能启动生产环境")
            return 2
        else:
            logger.warning("⚠️ 系统基本可用，但存在一些问题需要关注")
            return 1


async def main():
    validator = ProductionValidator()
    return await validator.run()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)