#!/usr/bin/env python3
"""测试和初始化持仓的止损止盈设置"""

import asyncio
from datetime import datetime, timedelta
import numpy as np
from loguru import logger
from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient
from longport_quant.features.technical_indicators import TechnicalIndicators

class PositionStopManager:
    """持仓止损止盈管理器"""

    def __init__(self):
        self.settings = get_settings()
        self.quote_client = QuoteDataClient(self.settings)
        self.trade_client = LongportTradingClient(self.settings)

        # ATR参数
        self.atr_period = 14
        self.atr_stop_multiplier = 2.0  # 止损 = ATR × 2
        self.atr_profit_multiplier = 3.0  # 止盈 = ATR × 3

        # 存储止损止盈位
        self.positions_with_stops = {}

    def _normalize_hk_symbol(self, symbol):
        """标准化港股代码"""
        if symbol.endswith('.HK'):
            code = symbol[:-3]
            if len(code) < 4 and code.isdigit():
                return f"{code.zfill(4)}.HK"
        return symbol

    async def get_positions(self):
        """获取账户持仓"""
        positions_resp = await self.trade_client.stock_positions()
        positions = {}

        for channel in positions_resp.channels:
            for pos in channel.positions:
                symbol = self._normalize_hk_symbol(pos.symbol)
                positions[symbol] = {
                    "quantity": pos.quantity,
                    "cost": float(pos.cost_price) if pos.cost_price else 0,
                    "currency": pos.currency,
                    "market": pos.market
                }

        return positions

    async def calculate_stops(self, symbol, entry_price):
        """计算止损止盈位"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=60)

            logger.info(f"  获取 {symbol} 的历史数据...")
            candles = await self.quote_client.get_history_candles(
                symbol=symbol,
                period=openapi.Period.Day,
                adjust_type=openapi.AdjustType.NoAdjust,
                start=start_date,
                end=end_date
            )

            if candles and len(candles) >= self.atr_period:
                highs = np.array([float(c.high) for c in candles])
                lows = np.array([float(c.low) for c in candles])
                closes = np.array([float(c.close) for c in candles])

                # 计算ATR
                atr = TechnicalIndicators.atr(highs, lows, closes, self.atr_period)
                current_atr = atr[-1]

                if not np.isnan(current_atr):
                    stop_loss = entry_price - current_atr * self.atr_stop_multiplier
                    take_profit = entry_price + current_atr * self.atr_profit_multiplier

                    return {
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "atr": current_atr
                    }
                else:
                    logger.warning(f"  ATR计算结果为NaN")
            else:
                logger.warning(f"  历史数据不足 (需要{self.atr_period}天，获得{len(candles) if candles else 0}天)")

        except Exception as e:
            logger.error(f"  计算止损止盈失败: {e}")

        # 使用默认百分比
        logger.info(f"  使用默认百分比计算止损止盈")
        return {
            "stop_loss": entry_price * 0.95,  # 5%止损
            "take_profit": entry_price * 1.15,  # 15%止盈
            "atr": None
        }

    async def analyze_positions(self):
        """分析所有持仓的止损止盈状态"""
        logger.info("=" * 60)
        logger.info("持仓止损止盈分析")
        logger.info("=" * 60)

        # 获取持仓
        positions = await self.get_positions()

        if not positions:
            logger.warning("没有找到任何持仓")
            return

        logger.info(f"\n发现 {len(positions)} 个持仓:")
        for symbol, pos in positions.items():
            logger.info(f"  {symbol}: {pos['quantity']}股 @ ${pos['cost']:.2f}")

        # 获取实时行情
        logger.info(f"\n获取实时行情...")
        symbols = list(positions.keys())
        quotes = await self.quote_client.get_realtime_quote(symbols)

        logger.info(f"\n计算止损止盈位...")
        logger.info("-" * 40)

        for symbol, pos in positions.items():
            logger.info(f"\n📊 {symbol}:")
            entry_price = pos['cost']

            if entry_price <= 0:
                logger.warning(f"  成本价为0，跳过")
                continue

            # 计算止损止盈
            stops = await self.calculate_stops(symbol, entry_price)
            self.positions_with_stops[symbol] = stops

            # 获取当前价格
            current_price = 0
            for quote in quotes:
                if quote.symbol == symbol:
                    current_price = float(quote.last_done) if quote.last_done else 0
                    break

            if current_price <= 0:
                logger.warning(f"  无法获取当前价格")
                continue

            # 计算盈亏
            pnl_pct = (current_price / entry_price - 1) * 100

            # 显示分析结果
            logger.info(f"  成本价: ${entry_price:.2f}")
            logger.info(f"  当前价: ${current_price:.2f}")
            logger.info(f"  盈亏: {pnl_pct:+.2f}%")

            if stops['atr']:
                logger.info(f"  ATR: ${stops['atr']:.2f}")

            logger.info(f"  止损位: ${stops['stop_loss']:.2f} (距离: {(current_price/stops['stop_loss']-1)*100:+.1f}%)")
            logger.info(f"  止盈位: ${stops['take_profit']:.2f} (距离: {(stops['take_profit']/current_price-1)*100:+.1f}%)")

            # 检查状态
            if current_price <= stops['stop_loss']:
                logger.error(f"  🛑 **已触发止损！应立即卖出**")
            elif current_price >= stops['take_profit']:
                logger.success(f"  🎉 **已触发止盈！可以考虑获利了结**")
            elif current_price < stops['stop_loss'] * 1.05:
                logger.warning(f"  ⚠️ 接近止损位，需要密切关注")
            elif current_price > stops['take_profit'] * 0.9:
                logger.info(f"  📈 接近止盈位")
            else:
                logger.info(f"  ✅ 正常持仓状态")

        # 总结
        logger.info("\n" + "=" * 60)
        logger.info("分析总结")
        logger.info("=" * 60)

        triggered_stops = []
        triggered_profits = []
        near_stops = []

        for symbol, pos in positions.items():
            if symbol not in self.positions_with_stops:
                continue

            stops = self.positions_with_stops[symbol]

            # 获取当前价格
            current_price = 0
            for quote in quotes:
                if quote.symbol == symbol:
                    current_price = float(quote.last_done) if quote.last_done else 0
                    break

            if current_price <= 0:
                continue

            entry_price = pos['cost']
            pnl_pct = (current_price / entry_price - 1) * 100

            if current_price <= stops['stop_loss']:
                triggered_stops.append((symbol, pnl_pct))
            elif current_price >= stops['take_profit']:
                triggered_profits.append((symbol, pnl_pct))
            elif current_price < stops['stop_loss'] * 1.05:
                near_stops.append((symbol, pnl_pct))

        if triggered_stops:
            logger.error(f"\n🛑 需要立即止损的持仓 ({len(triggered_stops)}个):")
            for sym, pnl in triggered_stops:
                logger.error(f"  {sym}: {pnl:.1f}%")

        if triggered_profits:
            logger.success(f"\n🎉 可以止盈的持仓 ({len(triggered_profits)}个):")
            for sym, pnl in triggered_profits:
                logger.success(f"  {sym}: +{pnl:.1f}%")

        if near_stops:
            logger.warning(f"\n⚠️ 接近止损的持仓 ({len(near_stops)}个):")
            for sym, pnl in near_stops:
                logger.warning(f"  {sym}: {pnl:.1f}%")

        if not triggered_stops and not triggered_profits and not near_stops:
            logger.info("\n✅ 所有持仓都在正常范围内")

        # 保存止损止盈设置到文件（可选）
        logger.info(f"\n💾 止损止盈设置已计算完成")
        logger.info(f"   共设置 {len(self.positions_with_stops)} 个持仓的止损止盈")

async def main():
    manager = PositionStopManager()
    await manager.analyze_positions()

if __name__ == "__main__":
    asyncio.run(main())