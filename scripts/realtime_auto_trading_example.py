#!/usr/bin/env python3
"""完整的实时自动交易示例 - 集成所有必要模块"""

import asyncio
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo
from loguru import logger

from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.utils import LotSizeHelper


class RealtimeAutoTrader:
    """实时自动交易系统（完整示例）"""

    def __init__(self):
        """初始化交易系统（模拟盘API，直接真实交易）"""
        self.settings = get_settings()
        self.beijing_tz = ZoneInfo('Asia/Shanghai')

        # 交易参数
        self.budget_per_stock = 5000  # 每只股票预算（港币/美元）
        self.max_positions = 5  # 最大持仓数量
        self.executed_today = set()  # 今日已交易标的

        # 手数辅助工具
        self.lot_size_helper = LotSizeHelper()

        logger.info("初始化自动交易系统（模拟盘API）")

    async def run(self):
        """主运行循环"""
        logger.info("=" * 60)
        logger.info("启动实时自动交易系统")
        logger.info("=" * 60)

        # 初始化客户端
        async with QuoteDataClient(self.settings) as quote_client, \
                   LongportTradingClient(self.settings) as trade_client:

            self.quote_client = quote_client
            self.trade_client = trade_client

            # 加载自选股
            watchlist = WatchlistLoader().load()
            symbols = list(watchlist.symbols())
            logger.info(f"监控 {len(symbols)} 个标的: {symbols}")

            # 检查账户状态
            account_status = await self.check_account_status()
            logger.info(f"账户余额: {account_status}")

            # 主循环
            iteration = 0
            while True:
                iteration += 1
                logger.info(f"\n{'='*60}")
                logger.info(f"第 {iteration} 轮扫描 - {datetime.now(self.beijing_tz).strftime('%H:%M:%S')}")
                logger.info(f"{'='*60}")

                try:
                    # 1. 检查是否在交易时段
                    if not self.is_trading_time():
                        logger.info("⏰ 不在交易时段，跳过本轮")
                        await asyncio.sleep(60)
                        continue

                    # 2. 获取实时行情
                    quotes = await self.get_realtime_quotes(symbols)
                    logger.info(f"📊 获取到 {len(quotes)} 个标的的实时行情")

                    # 3. 检查持仓和资金
                    account = await self.check_account_status()

                    # 4. 执行交易逻辑
                    signals = await self.generate_signals(quotes, account)

                    # 5. 执行订单
                    if signals:
                        logger.info(f"🎯 生成 {len(signals)} 个交易信号")
                        for signal in signals:
                            await self.execute_signal(signal, account)
                    else:
                        logger.info("💤 本轮无交易信号")

                except Exception as e:
                    logger.error(f"❌ 交易循环出错: {e}")

                # 等待下一轮（1分钟）
                logger.info("\n⏳ 等待60秒进入下一轮...")
                await asyncio.sleep(60)

    def is_trading_time(self):
        """检查是否在交易时段"""
        now = datetime.now(self.beijing_tz)
        current_time = now.time()

        # 周末不交易
        if now.weekday() >= 5:
            return False

        # 港股交易时段：9:30-12:00, 13:00-16:00
        hk_morning = time(9, 30) <= current_time <= time(12, 0)
        hk_afternoon = time(13, 0) <= current_time <= time(16, 0)

        # 美股交易时段（北京时间）：21:30-次日4:00
        us_trading = current_time >= time(21, 30) or current_time <= time(4, 0)

        return hk_morning or hk_afternoon or us_trading

    async def get_realtime_quotes(self, symbols):
        """获取实时行情"""
        try:
            quotes = await self.quote_client.get_realtime_quote(symbols)
            return quotes
        except Exception as e:
            logger.error(f"获取行情失败: {e}")
            return []

    async def check_account_status(self):
        """检查账户状态"""
        try:
            # 查询余额
            balances = await self.trade_client.account_balance()

            # 查询持仓
            positions_resp = await self.trade_client.stock_positions()

            # 解析余额（正确的属性名）
            cash = {}
            for balance in balances:
                # 使用 total_cash 而不是 cash
                cash[balance.currency] = float(balance.total_cash)

            # 解析持仓（使用正确的属性名）
            positions = {}
            for channel in positions_resp.channels:
                for pos in channel.positions:
                    positions[pos.symbol] = {
                        "quantity": pos.quantity,
                        "available_quantity": pos.available_quantity,
                        "cost": float(pos.cost_price) if pos.cost_price else 0,
                        "currency": pos.currency,
                        "market": pos.market
                    }

            return {
                "cash": cash,
                "positions": positions,
                "position_count": len(positions)
            }

        except Exception as e:
            logger.error(f"查询账户状态失败: {e}")
            import traceback
            traceback.print_exc()
            return {
                "cash": {"HKD": 0, "USD": 0},
                "positions": {},
                "position_count": 0
            }

    async def generate_signals(self, quotes, account):
        """生成交易信号"""
        signals = []

        for quote in quotes:
            symbol = quote.symbol
            price = float(quote.last_done)

            # 规则1：今天已经交易过，跳过
            if symbol in self.executed_today:
                logger.debug(f"  ⏭️  {symbol}: 今日已交易")
                continue

            # 规则2：已达到最大持仓数，跳过
            if account["position_count"] >= self.max_positions:
                logger.debug(f"  ⏭️  {symbol}: 已达最大持仓数")
                continue

            # 规则3：已经持有该标的，跳过
            if symbol in account["positions"]:
                logger.debug(f"  ⏭️  {symbol}: 已持有")
                continue

            # 规则4：简单策略 - 价格合理且成交量充足
            if self.check_buy_condition(quote):
                # 获取交易手数并计算数量
                lot_size = await self.lot_size_helper.get_lot_size(symbol, self.quote_client)
                quantity = self.lot_size_helper.calculate_order_quantity(
                    symbol, self.budget_per_stock, price, lot_size
                )
                if quantity > 0:
                    num_lots = quantity // lot_size
                    signal = {
                        "symbol": symbol,
                        "side": "BUY",
                        "price": price,
                        "quantity": quantity,
                        "lot_size": lot_size,
                        "num_lots": num_lots,
                        "reason": "价格合理且成交量充足"
                    }
                    signals.append(signal)
                    logger.info(f"  ✅ {symbol}: 生成买入信号 - {quantity}股 ({num_lots}手 × {lot_size}股/手) @ ${price:.2f}")

        return signals

    def check_buy_condition(self, quote):
        """检查买入条件（简单示例）"""
        # 条件1：价格 > 0
        if float(quote.last_done) <= 0:
            return False

        # 条件2：成交量 > 0
        if quote.volume <= 0:
            return False

        # 条件3：涨幅不超过5%（避免追高）
        if quote.prev_close and quote.last_done > 0:
            change_pct = (quote.last_done - quote.prev_close) / quote.prev_close * 100
            if abs(change_pct) > 5:
                return False

        return True

    async def execute_signal(self, signal, account):
        """执行交易信号"""
        symbol = signal["symbol"]
        side = signal["side"]
        price = signal["price"]
        quantity = signal["quantity"]
        lot_size = signal.get("lot_size", 1)
        num_lots = signal.get("num_lots", quantity)
        required_cash = price * quantity

        # 检查资金是否充足
        currency = "HKD" if ".HK" in symbol else "USD"
        available_cash = account["cash"].get(currency, 0)

        if required_cash > available_cash:
            logger.warning(
                f"  ⚠️  {symbol}: 资金不足 "
                f"(需要 ${required_cash:.2f}, 可用 ${available_cash:.2f})"
            )
            return

        # 实际下单（模拟盘API）
        try:
            order = await self.trade_client.submit_order({
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price
            })
            logger.success(
                f"  ✅ 订单已提交: {order['order_id']} - "
                f"{side} {symbol} {quantity}股 ({num_lots}手 × {lot_size}股/手) @ ${price:.2f} "
                f"(总额: ${required_cash:.2f})"
            )

            # 标记为已交易
            self.executed_today.add(symbol)

        except Exception as e:
            logger.error(f"  ❌ 下单失败: {e}")
            import traceback
            traceback.print_exc()


async def main():
    """主函数"""
    logger.info("使用模拟盘API，所有订单都是模拟交易")

    trader = RealtimeAutoTrader()

    try:
        await trader.run()
    except KeyboardInterrupt:
        logger.info("\n收到中断信号，停止交易系统")


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════╗
║           实时自动交易系统 - 模拟盘API                        ║
╠═══════════════════════════════════════════════════════════════╣
║                                                               ║
║  功能：                                                       ║
║  ✅ 实时行情监控（每60秒）                                    ║
║  ✅ 账户资金查询                                              ║
║  ✅ 持仓管理                                                  ║
║  ✅ 自动交易信号生成                                          ║
║  ✅ 风险控制（资金、持仓限制）                                ║
║  ✅ 自动下单（模拟盘）                                        ║
║                                                               ║
║  配置文件：configs/watchlist_test.yml                         ║
║  交易参数：                                                   ║
║    - 每只股票预算: $5,000                                     ║
║    - 最大持仓数量: 5只                                        ║
║    - 每只股票每天最多交易1次                                  ║
║                                                               ║
║  启动命令：                                                   ║
║  python3 scripts/realtime_auto_trading_example.py            ║
║                                                               ║
║  按 Ctrl+C 停止                                               ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
    """)

    asyncio.run(main())