#!/usr/bin/env python3
"""分析交易系统运行状态 - 诊断为什么没有产生订单"""

import asyncio
from datetime import datetime, timedelta
import numpy as np
from loguru import logger
from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient
from longport_quant.features.technical_indicators import TechnicalIndicators
from longport_quant.data.watchlist import WatchlistLoader

class TradingSystemAnalyzer:
    """交易系统分析器"""

    def __init__(self):
        self.settings = get_settings()
        self.quote_client = QuoteDataClient(self.settings)
        self.trade_client = LongportTradingClient(self.settings)

        # 交易参数（与主脚本一致）
        self.max_positions = 10
        self.max_daily_trades_per_symbol = 2

        # 信号阈值
        self.strong_buy_threshold = 60  # 强买入
        self.buy_threshold = 45         # 买入
        self.weak_buy_threshold = 30    # 弱买入

        # 内置监控列表（部分）
        self.hk_watchlist = {
            "0700.HK": {"name": "腾讯", "sector": "科技"},
            "9988.HK": {"name": "阿里巴巴", "sector": "科技"},
            "3690.HK": {"name": "美团", "sector": "科技"},
            "1810.HK": {"name": "小米", "sector": "科技"},
            "0981.HK": {"name": "中芯国际", "sector": "半导体"},
            "1211.HK": {"name": "比亚迪", "sector": "汽车"},
        }

    async def analyze_system(self):
        """全面分析系统状态"""
        logger.info("=" * 70)
        logger.info("📊 交易系统运行状态分析")
        logger.info("=" * 70)

        # 1. 检查账户状态
        account_info = await self.check_account_status()

        # 2. 分析持仓情况
        await self.analyze_positions(account_info)

        # 3. 检查交易限制
        await self.check_trading_constraints(account_info)

        # 4. 分析今日信号
        await self.analyze_today_signals()

        # 5. 检查历史订单
        await self.check_order_history()

        # 6. 分析为什么没有订单
        await self.diagnose_no_orders(account_info)

    async def check_account_status(self):
        """检查账户状态"""
        logger.info("\n🏦 账户状态检查")
        logger.info("-" * 50)

        balances = await self.trade_client.account_balance()
        positions_resp = await self.trade_client.stock_positions()

        # 统计现金
        total_cash = 0
        for balance in balances:
            currency = balance.currency
            buy_power = float(balance.buy_power) if hasattr(balance, 'buy_power') else 0
            logger.info(f"   {currency}: 购买力 ${buy_power:,.2f}")
            if currency == "HKD":
                total_cash += buy_power

        # 统计持仓
        positions = {}
        total_market_value = 0
        for channel in positions_resp.channels:
            for pos in channel.positions:
                symbol = pos.symbol
                positions[symbol] = {
                    "quantity": pos.quantity,
                    "cost": float(pos.cost_price) if pos.cost_price else 0,
                    "currency": pos.currency
                }
                # 估算市值
                market_value = float(pos.cost_price) * float(pos.quantity) if pos.cost_price else 0
                total_market_value += market_value

        logger.info(f"\n   持仓数量: {len(positions)}/{self.max_positions}")
        logger.info(f"   持仓市值: ${total_market_value:,.2f}")
        logger.info(f"   账户总值: ${total_cash + total_market_value:,.2f}")

        if len(positions) >= self.max_positions:
            logger.warning(f"   ⚠️ 已达最大持仓数！无法开新仓")

        return {
            "cash": total_cash,
            "positions": positions,
            "position_count": len(positions),
            "market_value": total_market_value,
            "is_full": len(positions) >= self.max_positions
        }

    async def analyze_positions(self, account_info):
        """分析持仓状况"""
        logger.info("\n📦 持仓分析")
        logger.info("-" * 50)

        if not account_info["positions"]:
            logger.info("   无持仓")
            return

        # 获取实时行情
        symbols = list(account_info["positions"].keys())
        quotes = await self.quote_client.get_realtime_quote(symbols)

        total_pnl = 0
        winners = 0
        losers = 0

        for quote in quotes:
            symbol = quote.symbol
            if symbol in account_info["positions"]:
                pos = account_info["positions"][symbol]
                current_price = float(quote.last_done) if quote.last_done else 0
                cost = pos["cost"]

                if current_price > 0 and cost > 0:
                    pnl_pct = (current_price / cost - 1) * 100
                    pnl_amount = (current_price - cost) * float(pos["quantity"])
                    total_pnl += pnl_amount

                    status = "🟢" if pnl_pct > 0 else "🔴"
                    logger.info(f"   {status} {symbol}: {pnl_pct:+.2f}% (${pnl_amount:+,.0f})")

                    if pnl_pct > 0:
                        winners += 1
                    else:
                        losers += 1

                    # 检查止损止盈状态
                    if pnl_pct < -10:
                        logger.warning(f"      ⚠️ 亏损超过10%，需要关注止损")
                    elif pnl_pct > 20:
                        logger.success(f"      ✅ 盈利超过20%，可以考虑止盈")

        logger.info(f"\n   总盈亏: ${total_pnl:+,.0f}")
        logger.info(f"   赢/输: {winners}/{losers}")

    async def check_trading_constraints(self, account_info):
        """检查交易限制"""
        logger.info("\n🚦 交易限制检查")
        logger.info("-" * 50)

        # 检查持仓限制
        if account_info["is_full"]:
            logger.error("   ❌ 持仓已满，无法开新仓")
        else:
            slots = self.max_positions - account_info["position_count"]
            logger.success(f"   ✅ 还可开仓: {slots}个")

        # 检查资金限制
        min_order_amount = 1000  # 最小下单金额
        if account_info["cash"] < min_order_amount:
            logger.error(f"   ❌ 现金不足 (${account_info['cash']:.0f} < ${min_order_amount})")
        else:
            logger.success(f"   ✅ 资金充足: ${account_info['cash']:,.0f}")

        # 检查交易时间
        now = datetime.now()
        hour = now.hour

        logger.info(f"\n   当前时间: {now.strftime('%H:%M:%S')}")

        # 港股交易时间 (9:30-12:00, 13:00-16:00)
        if 9 <= hour < 12 or 13 <= hour < 16:
            logger.success("   ✅ 港股交易时间")
        else:
            logger.warning("   ⚠️ 港股非交易时间")

    async def analyze_today_signals(self):
        """分析今日可能的交易信号"""
        logger.info("\n📈 交易信号分析")
        logger.info("-" * 50)

        # 分析几个主要标的
        test_symbols = ["0700.HK", "9988.HK", "3690.HK", "0981.HK", "1211.HK"]

        signal_count = {"strong": 0, "normal": 0, "weak": 0, "no_signal": 0}

        for symbol in test_symbols:
            try:
                # 获取实时行情
                quotes = await self.quote_client.get_realtime_quote([symbol])
                if not quotes:
                    continue

                current_price = float(quotes[0].last_done) if quotes[0].last_done else 0
                if current_price <= 0:
                    continue

                # 简化的信号评分
                score = await self.calculate_signal_score(symbol, current_price)

                name = self.hk_watchlist.get(symbol, {}).get("name", symbol)

                if score >= self.strong_buy_threshold:
                    logger.success(f"   🟢 {symbol} ({name}): 强买入信号 (评分:{score})")
                    signal_count["strong"] += 1
                elif score >= self.buy_threshold:
                    logger.info(f"   🟡 {symbol} ({name}): 买入信号 (评分:{score})")
                    signal_count["normal"] += 1
                elif score >= self.weak_buy_threshold:
                    logger.warning(f"   🟠 {symbol} ({name}): 弱买入信号 (评分:{score})")
                    signal_count["weak"] += 1
                else:
                    logger.debug(f"   ⚪ {symbol} ({name}): 无信号 (评分:{score})")
                    signal_count["no_signal"] += 1

            except Exception as e:
                logger.error(f"   分析 {symbol} 失败: {e}")

        logger.info(f"\n   信号统计:")
        logger.info(f"   强买入: {signal_count['strong']}个")
        logger.info(f"   普通买入: {signal_count['normal']}个")
        logger.info(f"   弱买入: {signal_count['weak']}个")
        logger.info(f"   无信号: {signal_count['no_signal']}个")

    async def calculate_signal_score(self, symbol, current_price):
        """简化的信号评分计算"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=60)

            candles = await self.quote_client.get_history_candles(
                symbol=symbol,
                period=openapi.Period.Day,
                adjust_type=openapi.AdjustType.NoAdjust,
                start=start_date,
                end=end_date
            )

            if not candles or len(candles) < 20:
                return 0

            closes = np.array([float(c.close) for c in candles])

            # 计算RSI
            rsi = TechnicalIndicators.rsi(closes, period=14)
            current_rsi = rsi[-1] if len(rsi) > 0 else 50

            # 计算布林带
            bb = TechnicalIndicators.bollinger_bands(closes, period=20, std_dev=2)
            bb_lower = bb['lower'][-1] if 'lower' in bb else 0
            bb_upper = bb['upper'][-1] if 'upper' in bb else 0
            bb_middle = bb['middle'][-1] if 'middle' in bb else 0

            # 简化评分
            score = 0

            # RSI评分
            if current_rsi < 30:
                score += 30
            elif current_rsi < 40:
                score += 15
            elif current_rsi < 50:
                score += 5

            # 布林带评分
            if current_price <= bb_lower:
                score += 25
            elif current_price < bb_middle:
                score += 10

            # 趋势评分
            sma20 = np.mean(closes[-20:]) if len(closes) >= 20 else 0
            sma50 = np.mean(closes[-50:]) if len(closes) >= 50 else 0
            if sma20 > sma50:
                score += 10

            return score

        except:
            return 0

    async def check_order_history(self):
        """检查历史订单"""
        logger.info("\n📜 今日订单历史")
        logger.info("-" * 50)

        try:
            # 获取今日订单
            today = datetime.now().date()
            orders = await self.trade_client.today_orders()

            if orders:
                logger.info(f"   找到 {len(orders)} 个订单:")
                for order in orders[:5]:  # 显示前5个
                    logger.info(f"   - {order.symbol}: {order.side} {order.quantity}股 @ ${order.price}")
                    logger.info(f"     状态: {order.status}")
            else:
                logger.warning("   ⚠️ 今日暂无订单")

        except Exception as e:
            logger.error(f"   获取订单失败: {e}")

    async def diagnose_no_orders(self, account_info):
        """诊断为什么没有产生订单"""
        logger.info("\n🔍 诊断：为什么没有产生订单？")
        logger.info("-" * 50)

        reasons = []

        # 1. 持仓已满
        if account_info["is_full"]:
            reasons.append("持仓数已达上限(10个)，无法开新仓")

        # 2. 资金不足
        if account_info["cash"] < 5000:
            reasons.append(f"现金不足(${account_info['cash']:.0f})，可能无法满足最小下单要求")

        # 3. 信号阈值过高
        reasons.append(f"买入信号阈值较高(弱:{self.weak_buy_threshold}/普通:{self.buy_threshold}/强:{self.strong_buy_threshold})")
        reasons.append("当前市场可能没有足够强的超卖信号")

        # 4. 策略特性
        reasons.append("策略倾向于逆势买入(RSI超卖+触及布林带下轨)")
        reasons.append("在市场平稳或上涨时较少产生买入信号")

        # 5. 止损止盈未触发
        reasons.append("现有持仓可能未达到止损止盈位")

        logger.info("\n   可能的原因:")
        for i, reason in enumerate(reasons, 1):
            logger.info(f"   {i}. {reason}")

        # 建议
        logger.info("\n💡 建议:")
        logger.info("   1. 检查是否需要清理部分弱势持仓腾出仓位")
        logger.info("   2. 考虑降低信号阈值(如weak_buy从30降到25)")
        logger.info("   3. 等待市场回调出现超卖信号")
        logger.info("   4. 检查止损位设置是否过宽(ATR×2可能太宽)")
        logger.info("   5. 考虑手动干预处理亏损较大的持仓")

async def main():
    analyzer = TradingSystemAnalyzer()
    await analyzer.analyze_system()

if __name__ == "__main__":
    asyncio.run(main())