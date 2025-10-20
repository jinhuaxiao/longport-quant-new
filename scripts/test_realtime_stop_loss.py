#!/usr/bin/env python3
"""测试实时止损止盈功能"""

import asyncio
from datetime import datetime
from loguru import logger
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.longport_quant.config import get_settings
from src.longport_quant.execution.client import LongportTradingClient
from src.longport_quant.data.quote_client import QuoteDataClient
from src.longport_quant.persistence.stop_manager import StopLossManager


class RealtimeStopLossTest:
    def __init__(self):
        self.settings = get_settings()
        self.stop_manager = StopLossManager()
        self.positions_with_stops = {}
        self._cached_account = None

    async def check_realtime_stop_loss(self, symbol, current_price, position):
        """测试实时止损止盈检查函数"""
        try:
            # 检查是否有设置止损止盈
            if symbol not in self.positions_with_stops:
                # 尝试从数据库加载
                stop_data = await self.stop_manager.get_stop_for_symbol(symbol)
                if stop_data:
                    self.positions_with_stops[symbol] = stop_data
                    logger.info(f"📂 从数据库加载 {symbol} 的止损止盈设置")
                else:
                    logger.warning(f"未找到 {symbol} 的止损止盈设置")
                    return False, None

            stops = self.positions_with_stops[symbol]
            stop_loss = stops["stop_loss"]
            take_profit = stops["take_profit"]
            entry_price = position["cost"]

            # 计算盈亏
            pnl_pct = (current_price / entry_price - 1) * 100

            logger.info(
                f"📊 检查 {symbol}: 当前价=${current_price:.2f}, "
                f"止损=${stop_loss:.2f}, 止盈=${take_profit:.2f}, 盈亏={pnl_pct:+.1f}%"
            )

            # 检查止损
            if current_price <= stop_loss:
                logger.warning(f"🛑 {symbol} 触发止损! 当前价${current_price:.2f} <= 止损位${stop_loss:.2f}")
                return True, "STOP_LOSS"

            # 检查止盈
            elif current_price >= take_profit:
                logger.success(f"🎉 {symbol} 触发止盈! 当前价${current_price:.2f} >= 止盈位${take_profit:.2f}")
                return True, "TAKE_PROFIT"

            logger.info(f"✅ {symbol} 未触发止损止盈")
            return False, None

        except Exception as e:
            logger.error(f"检查失败: {e}")
            return False, None

    async def test_with_positions(self):
        """测试实际持仓的止损止盈"""
        async with LongportTradingClient(self.settings) as trade_client, \
                   QuoteDataClient(self.settings) as quote_client:

            # 获取账户持仓
            logger.info("获取账户持仓...")
            positions_resp = await trade_client.stock_positions()

            positions = {}
            for channel in positions_resp.channels:
                for pos in channel.positions:
                    symbol = pos.symbol
                    if symbol.endswith('.HK'):
                        # 标准化港股代码
                        code = symbol[:-3]
                        if len(code) < 4 and code.isdigit():
                            code = code.zfill(4)
                            symbol = f"{code}.HK"

                    positions[symbol] = {
                        "quantity": pos.quantity,
                        "cost": float(pos.cost_price) if pos.cost_price else 0
                    }

            if not positions:
                logger.warning("当前无持仓，无法测试")
                return

            logger.info(f"发现 {len(positions)} 个持仓:")
            for symbol in positions:
                logger.info(f"  {symbol}: {positions[symbol]['quantity']}股 @ ${positions[symbol]['cost']:.2f}")

            # 获取实时行情
            symbols = list(positions.keys())
            logger.info(f"\n获取 {len(symbols)} 个标的的实时行情...")
            quotes = []
            for symbol in symbols:
                try:
                    quote = await quote_client.get_realtime_quote([symbol])
                    if quote:
                        quotes.extend(quote)
                except:
                    pass

            # 测试每个持仓的止损止盈
            logger.info("\n开始测试实时止损止盈检查...")
            for quote in quotes:
                symbol = quote.symbol
                if symbol in positions:
                    current_price = float(quote.last_done)
                    position = positions[symbol]

                    logger.info(f"\n{'='*50}")
                    triggered, trigger_type = await self.check_realtime_stop_loss(
                        symbol, current_price, position
                    )

                    if triggered:
                        logger.info(f"💡 测试结果: {symbol} 将触发 {trigger_type}")
                    else:
                        logger.info(f"💡 测试结果: {symbol} 保持持有")

    async def test_simulation(self):
        """模拟测试止损止盈"""
        logger.info("\n模拟测试止损止盈...")

        # 创建模拟数据
        test_cases = [
            # (symbol, entry_price, stop_loss, take_profit, current_price, expected_trigger)
            ("TEST1.HK", 100, 95, 110, 94, "STOP_LOSS"),   # 触发止损
            ("TEST2.HK", 100, 95, 110, 111, "TAKE_PROFIT"), # 触发止盈
            ("TEST3.HK", 100, 95, 110, 105, None),          # 不触发
        ]

        for symbol, entry_price, stop_loss, take_profit, current_price, expected in test_cases:
            logger.info(f"\n测试案例: {symbol}")

            # 保存测试止损止盈
            await self.stop_manager.save_stop(
                symbol=symbol,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                quantity=100,
                strategy='test'
            )

            # 模拟持仓
            position = {"cost": entry_price, "quantity": 100}

            # 测试
            triggered, trigger_type = await self.check_realtime_stop_loss(
                symbol, current_price, position
            )

            if trigger_type == expected:
                logger.success(f"✅ 测试通过: 期望{expected}, 实际{trigger_type}")
            else:
                logger.error(f"❌ 测试失败: 期望{expected}, 实际{trigger_type}")

            # 清理测试数据
            await self.stop_manager.remove_stop(symbol)


async def main():
    logger.info("="*70)
    logger.info("实时止损止盈功能测试")
    logger.info("="*70)

    tester = RealtimeStopLossTest()

    # 1. 模拟测试
    await tester.test_simulation()

    # 2. 实际持仓测试
    logger.info("\n" + "="*70)
    logger.info("测试实际持仓...")
    await tester.test_with_positions()

    logger.info("\n测试完成！")


if __name__ == "__main__":
    asyncio.run(main())