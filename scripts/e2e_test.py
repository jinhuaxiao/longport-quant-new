#!/usr/bin/env python3
"""端到端测试脚本 - 模拟完整的交易流程"""

import asyncio
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Any
from decimal import Decimal
from loguru import logger
from sqlalchemy import text

from longport_quant.config import get_settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import Portfolio, Position, Order, Trade
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.data.kline_sync import KlineDataService
from longport_quant.strategies.base import StrategyBase
from longport_quant.strategies.ma_crossover import MovingAverageCrossoverStrategy
from longport_quant.risk.manager import RiskManager
from longport_quant.risk.checks import RiskLimits


class E2ETestRunner:
    """端到端测试运行器"""

    def __init__(self):
        self.settings = get_settings()
        self.test_results = {}
        self.test_portfolio_id = "test_portfolio_e2e"

    async def setup_test_environment(self):
        """设置测试环境"""
        logger.info("设置测试环境...")

        try:
            async with DatabaseSessionManager(self.settings.database_dsn) as db:
                async with db.session() as session:
                    # 清理旧的测试数据
                    await session.execute(
                        text("DELETE FROM trade WHERE portfolio_id = :id"),
                        {"id": self.test_portfolio_id}
                    )
                    await session.execute(
                        text("DELETE FROM \"order\" WHERE portfolio_id = :id"),
                        {"id": self.test_portfolio_id}
                    )
                    await session.execute(
                        text("DELETE FROM position WHERE portfolio_id = :id"),
                        {"id": self.test_portfolio_id}
                    )
                    await session.execute(
                        text("DELETE FROM portfolio WHERE id = :id"),
                        {"id": self.test_portfolio_id}
                    )
                    await session.commit()

                    # 创建测试投资组合
                    portfolio = Portfolio(
                        id=self.test_portfolio_id,
                        name="E2E Test Portfolio",
                        initial_capital=Decimal("1000000"),
                        current_capital=Decimal("1000000"),
                        active=True
                    )
                    session.add(portfolio)
                    await session.commit()

                    logger.info(f"✓ 创建测试投资组合: {self.test_portfolio_id}")
                    return True

        except Exception as e:
            logger.error(f"设置测试环境失败: {e}")
            return False

    async def test_data_sync(self):
        """测试数据同步"""
        logger.info("测试数据同步...")

        try:
            db = DatabaseSessionManager(self.settings.database_dsn, auto_init=True)
            quote_client = QuoteDataClient(self.settings)
            kline_service = KlineDataService(self.settings, db, quote_client)

            # 测试同步单个股票的最新数据
            test_symbol = "700.HK"
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=30)

            results = await kline_service.sync_daily_klines(
                symbols=[test_symbol],
                start_date=start_date,
                end_date=end_date
            )

            await db.close()

            if test_symbol in results and results[test_symbol] >= 0:
                logger.info(f"✓ 数据同步测试通过: {test_symbol} 同步了 {results[test_symbol]} 条记录")
                self.test_results["data_sync"] = True
                return True
            else:
                logger.error(f"✗ 数据同步测试失败")
                self.test_results["data_sync"] = False
                return False

        except Exception as e:
            logger.error(f"数据同步测试异常: {e}")
            self.test_results["data_sync"] = False
            return False

    async def test_strategy_signal_generation(self):
        """测试策略信号生成"""
        logger.info("测试策略信号生成...")

        try:
            # 初始化策略
            strategy = MovingAverageCrossoverStrategy(
                short_window=5,
                long_window=20
            )

            # 获取测试数据
            test_symbol = "700.HK"
            async with DatabaseSessionManager(self.settings.database_dsn) as db:
                async with db.session() as session:
                    # 获取历史K线数据
                    result = await session.execute(
                        text("""
                            SELECT timestamp, open, high, low, close, volume
                            FROM kline_daily
                            WHERE symbol = :symbol
                            ORDER BY timestamp DESC
                            LIMIT 50
                        """),
                        {"symbol": test_symbol}
                    )

                    klines = []
                    for row in result:
                        klines.append({
                            "timestamp": row.timestamp,
                            "open": float(row.open),
                            "high": float(row.high),
                            "low": float(row.low),
                            "close": float(row.close),
                            "volume": int(row.volume)
                        })

                    if len(klines) < 20:
                        logger.warning(f"数据不足，只有 {len(klines)} 条K线")
                        self.test_results["strategy_signal"] = False
                        return False

                    # 生成交易信号
                    klines.reverse()  # 按时间正序
                    signals = await strategy.generate_signals([test_symbol], {test_symbol: klines})

                    if signals is not None:
                        logger.info(f"✓ 策略信号生成测试通过，生成了 {len(signals) if signals else 0} 个信号")
                        self.test_results["strategy_signal"] = True
                        return True
                    else:
                        logger.info("✓ 策略信号生成测试通过（无信号）")
                        self.test_results["strategy_signal"] = True
                        return True

        except Exception as e:
            logger.error(f"策略信号生成测试异常: {e}")
            self.test_results["strategy_signal"] = False
            return False

    async def test_risk_management(self):
        """测试风险管理"""
        logger.info("测试风险管理...")

        try:
            # 创建风险管理器
            risk_limits = RiskLimits(
                max_position_size=Decimal("100000"),
                max_order_size=Decimal("10000"),
                max_daily_trades=100,
                max_portfolio_risk=Decimal("0.02")
            )

            risk_manager = RiskManager(
                portfolio_id=self.test_portfolio_id,
                limits=risk_limits,
                db=DatabaseSessionManager(self.settings.database_dsn, auto_init=True)
            )

            # 测试风险检查
            from longport_quant.common.types import Signal

            # 测试正常订单
            normal_signal = Signal(
                symbol="700.HK",
                side="BUY",
                quantity=100,
                price=300.0
            )

            # 测试超大订单
            large_signal = Signal(
                symbol="700.HK",
                side="BUY",
                quantity=1000000,  # 超过限制
                price=300.0
            )

            # 执行风险检查
            normal_check = await risk_manager.check_order_risk(normal_signal)
            large_check = await risk_manager.check_order_risk(large_signal)

            if normal_check and not large_check:
                logger.info("✓ 风险管理测试通过：正常订单通过，超限订单被拒绝")
                self.test_results["risk_management"] = True
                return True
            else:
                logger.error("✗ 风险管理测试失败：风险检查结果异常")
                self.test_results["risk_management"] = False
                return False

        except Exception as e:
            logger.error(f"风险管理测试异常: {e}")
            self.test_results["risk_management"] = False
            return False

    async def test_order_creation(self):
        """测试订单创建"""
        logger.info("测试订单创建...")

        try:
            async with DatabaseSessionManager(self.settings.database_dsn) as db:
                async with db.session() as session:
                    # 创建测试订单
                    test_order = Order(
                        portfolio_id=self.test_portfolio_id,
                        symbol="700.HK",
                        side="BUY",
                        order_type="LIMIT",
                        quantity=100,
                        price=Decimal("300.00"),
                        status="PENDING",
                        created_at=datetime.now()
                    )

                    session.add(test_order)
                    await session.commit()

                    # 查询订单
                    result = await session.execute(
                        text("""
                            SELECT id, symbol, quantity, price, status
                            FROM "order"
                            WHERE portfolio_id = :portfolio_id
                            ORDER BY created_at DESC
                            LIMIT 1
                        """),
                        {"portfolio_id": self.test_portfolio_id}
                    )

                    order = result.first()
                    if order and order.symbol == "700.HK":
                        logger.info(f"✓ 订单创建测试通过: Order ID {order.id}")
                        self.test_results["order_creation"] = True
                        return True
                    else:
                        logger.error("✗ 订单创建测试失败")
                        self.test_results["order_creation"] = False
                        return False

        except Exception as e:
            logger.error(f"订单创建测试异常: {e}")
            self.test_results["order_creation"] = False
            return False

    async def test_position_update(self):
        """测试持仓更新"""
        logger.info("测试持仓更新...")

        try:
            async with DatabaseSessionManager(self.settings.database_dsn) as db:
                async with db.session() as session:
                    # 创建或更新持仓
                    position = Position(
                        portfolio_id=self.test_portfolio_id,
                        symbol="700.HK",
                        quantity=100,
                        avg_cost=Decimal("300.00"),
                        current_price=Decimal("310.00"),
                        market_value=Decimal("31000.00"),
                        unrealized_pnl=Decimal("1000.00"),
                        realized_pnl=Decimal("0"),
                        updated_at=datetime.now()
                    )

                    session.add(position)
                    await session.commit()

                    # 查询持仓
                    result = await session.execute(
                        text("""
                            SELECT symbol, quantity, avg_cost, unrealized_pnl
                            FROM position
                            WHERE portfolio_id = :portfolio_id
                        """),
                        {"portfolio_id": self.test_portfolio_id}
                    )

                    pos = result.first()
                    if pos and pos.quantity == 100:
                        logger.info(f"✓ 持仓更新测试通过: {pos.symbol} 数量 {pos.quantity}")
                        self.test_results["position_update"] = True
                        return True
                    else:
                        logger.error("✗ 持仓更新测试失败")
                        self.test_results["position_update"] = False
                        return False

        except Exception as e:
            logger.error(f"持仓更新测试异常: {e}")
            self.test_results["position_update"] = False
            return False

    async def test_portfolio_performance(self):
        """测试组合绩效计算"""
        logger.info("测试组合绩效计算...")

        try:
            async with DatabaseSessionManager(self.settings.database_dsn) as db:
                async with db.session() as session:
                    # 更新组合市值
                    await session.execute(
                        text("""
                            UPDATE portfolio
                            SET current_capital = initial_capital +
                                COALESCE((SELECT SUM(unrealized_pnl) FROM position WHERE portfolio_id = :id), 0),
                                updated_at = NOW()
                            WHERE id = :id
                        """),
                        {"id": self.test_portfolio_id}
                    )
                    await session.commit()

                    # 查询组合绩效
                    result = await session.execute(
                        text("""
                            SELECT
                                initial_capital,
                                current_capital,
                                (current_capital - initial_capital) / initial_capital * 100 as return_pct
                            FROM portfolio
                            WHERE id = :id
                        """),
                        {"id": self.test_portfolio_id}
                    )

                    perf = result.first()
                    if perf:
                        logger.info(f"✓ 组合绩效测试通过: 收益率 {perf.return_pct:.2f}%")
                        self.test_results["portfolio_performance"] = True
                        return True
                    else:
                        logger.error("✗ 组合绩效测试失败")
                        self.test_results["portfolio_performance"] = False
                        return False

        except Exception as e:
            logger.error(f"组合绩效测试异常: {e}")
            self.test_results["portfolio_performance"] = False
            return False

    async def cleanup_test_environment(self):
        """清理测试环境"""
        logger.info("清理测试环境...")

        try:
            async with DatabaseSessionManager(self.settings.database_dsn) as db:
                async with db.session() as session:
                    # 清理测试数据
                    await session.execute(
                        text("DELETE FROM trade WHERE portfolio_id = :id"),
                        {"id": self.test_portfolio_id}
                    )
                    await session.execute(
                        text("DELETE FROM \"order\" WHERE portfolio_id = :id"),
                        {"id": self.test_portfolio_id}
                    )
                    await session.execute(
                        text("DELETE FROM position WHERE portfolio_id = :id"),
                        {"id": self.test_portfolio_id}
                    )
                    await session.execute(
                        text("DELETE FROM portfolio WHERE id = :id"),
                        {"id": self.test_portfolio_id}
                    )
                    await session.commit()

                    logger.info("✓ 测试环境清理完成")
                    return True

        except Exception as e:
            logger.error(f"清理测试环境失败: {e}")
            return False

    async def run(self) -> int:
        """运行端到端测试"""
        logger.info("=" * 60)
        logger.info("开始端到端测试")
        logger.info("=" * 60)

        # 定义测试步骤
        test_steps = [
            ("设置测试环境", self.setup_test_environment),
            ("数据同步", self.test_data_sync),
            ("策略信号生成", self.test_strategy_signal_generation),
            ("风险管理", self.test_risk_management),
            ("订单创建", self.test_order_creation),
            ("持仓更新", self.test_position_update),
            ("组合绩效计算", self.test_portfolio_performance),
            ("清理测试环境", self.cleanup_test_environment),
        ]

        passed = 0
        failed = 0

        for step_name, step_func in test_steps:
            try:
                logger.info(f"\n运行: {step_name}")
                result = await step_func()
                if result:
                    passed += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"测试步骤 {step_name} 执行失败: {e}")
                failed += 1

        # 输出测试报告
        logger.info("\n" + "=" * 60)
        logger.info("端到端测试报告")
        logger.info("=" * 60)
        logger.info(f"通过测试: {passed}/{len(test_steps)}")
        logger.info(f"失败测试: {failed}/{len(test_steps)}")

        logger.info("\n详细结果:")
        for test_name, result in self.test_results.items():
            status = "✓" if result else "✗"
            logger.info(f"  {status} {test_name}")

        if failed == 0:
            logger.info("\n✅ 所有端到端测试通过！系统运行正常。")
            return 0
        else:
            logger.error(f"\n❌ {failed} 个测试失败，请检查系统配置。")
            return 1


async def main():
    runner = E2ETestRunner()
    return await runner.run()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)