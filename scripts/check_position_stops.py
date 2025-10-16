#!/usr/bin/env python3
"""检查当前持仓的止盈止损状态"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from loguru import logger
import numpy as np

from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient
from longport_quant.features.technical_indicators import TechnicalIndicators


async def check_positions():
    """检查持仓止盈止损状态"""
    settings = get_settings()

    logger.info("=" * 70)
    logger.info("检查持仓止盈止损状态")
    logger.info("=" * 70)

    async with QuoteDataClient(settings) as quote_client, \
               LongportTradingClient(settings) as trade_client:

        # 1. 获取账户信息
        account_balances = await trade_client.account_balance()
        logger.info(f"\n📊 账户余额:")
        for balance in account_balances:
            if balance.total_cash > 0:
                logger.info(f"   {balance.currency}: ${float(balance.total_cash):,.2f}")

        # 2. 获取持仓
        positions_response = await trade_client.stock_positions()
        stock_positions = positions_response.channels if positions_response.channels else []
        logger.info(f"\n📦 当前持仓: {len(stock_positions)} 个")

        if not stock_positions:
            logger.warning("   没有持仓")
            return

        # 3. 获取所有标的的实时行情
        symbols = []
        for channel in stock_positions:
            for position in channel.positions:
                symbols.append(position.symbol)
        logger.info(f"\n正在获取行情: {symbols}")

        quotes = await quote_client.get_realtime_quote(symbols)
        quote_map = {q.symbol: q for q in quotes}

        # 4. 分析每个持仓
        logger.info("\n" + "=" * 70)
        logger.info("持仓分析")
        logger.info("=" * 70)

        for channel in stock_positions:
            for position in channel.positions:
                symbol = position.symbol
                quantity = float(position.quantity)
                cost_price = float(position.cost_price)

                # 获取当前价格
                if symbol not in quote_map:
                    logger.warning(f"\n⚠️  {symbol}: 无法获取行情")
                    continue

                quote = quote_map[symbol]
                current_price = float(quote.last_done)
                prev_close = float(quote.prev_close) if quote.prev_close else cost_price

                # 计算盈亏
                total_cost = cost_price * quantity
                current_value = current_price * quantity
                pnl = current_value - total_cost
                pnl_pct = (current_price / cost_price - 1) * 100

                logger.info(f"\n{'='*70}")
                logger.info(f"📊 {symbol}")
                logger.info(f"{'='*70}")
                logger.info(f"   持仓数量: {quantity:.0f}股")
                logger.info(f"   成本价: ${cost_price:.2f}")
                logger.info(f"   当前价: ${current_price:.2f}")
                logger.info(f"   昨收价: ${prev_close:.2f}")
                logger.info(f"   持仓市值: ${current_value:,.2f}")

                # 盈亏显示
                if pnl > 0:
                    logger.success(f"   盈亏: +${pnl:,.2f} (+{pnl_pct:.2f}%)")
                elif pnl < 0:
                    logger.warning(f"   盈亏: -${abs(pnl):,.2f} ({pnl_pct:.2f}%)")
                else:
                    logger.info(f"   盈亏: $0.00 (0.00%)")

                # 计算ATR动态止损止盈
                try:
                    # 获取历史K线数据计算ATR
                    end_date = datetime.now()
                    start_date = end_date - timedelta(days=60)

                    candles = await quote_client.get_history_candles(
                        symbol=symbol,
                        period=openapi.Period.Day,
                        adjust_type=openapi.AdjustType.NoAdjust,
                        start=start_date,
                        end=end_date
                    )

                    if candles and len(candles) >= 14:
                        highs = np.array([float(c.high) for c in candles])
                        lows = np.array([float(c.low) for c in candles])
                        closes = np.array([float(c.close) for c in candles])

                        # 计算ATR
                        atr = TechnicalIndicators.atr(highs, lows, closes, period=14)
                        current_atr = atr[-1]

                        # 动态止损止盈 (基于成本价)
                        atr_stop_loss = cost_price - current_atr * 2.0  # ATR × 2
                        atr_take_profit = cost_price + current_atr * 3.0  # ATR × 3

                        # 固定比例止损止盈
                        fixed_stop_loss = cost_price * 0.95  # -5%
                        fixed_take_profit = cost_price * 1.15  # +15%

                        logger.info(f"\n   📊 技术指标:")
                        logger.info(f"      ATR(14): ${current_atr:.2f}")

                        logger.info(f"\n   🎯 动态止损止盈 (ATR):")
                        logger.info(f"      止损位: ${atr_stop_loss:.2f} ({(atr_stop_loss/cost_price-1)*100:.1f}%)")
                        logger.info(f"      止盈位: ${atr_take_profit:.2f} ({(atr_take_profit/cost_price-1)*100:.1f}%)")

                        logger.info(f"\n   📏 固定止损止盈:")
                        logger.info(f"      止损位: ${fixed_stop_loss:.2f} (-5.0%)")
                        logger.info(f"      止盈位: ${fixed_take_profit:.2f} (+15.0%)")

                        # 检查是否触发
                        logger.info(f"\n   ⚡ 触发状态:")

                        if current_price <= atr_stop_loss:
                            logger.error(f"      🛑 已触发ATR止损! (当前价 ${current_price:.2f} <= 止损位 ${atr_stop_loss:.2f})")
                        elif current_price <= fixed_stop_loss:
                            logger.warning(f"      ⚠️  已触发固定止损! (当前价 ${current_price:.2f} <= 止损位 ${fixed_stop_loss:.2f})")
                        elif current_price >= atr_take_profit:
                            logger.success(f"      🎉 已触发ATR止盈! (当前价 ${current_price:.2f} >= 止盈位 ${atr_take_profit:.2f})")
                        elif current_price >= fixed_take_profit:
                            logger.success(f"      ✅ 已触发固定止盈! (当前价 ${current_price:.2f} >= 止盈位 ${fixed_take_profit:.2f})")
                        else:
                            # 计算距离止损止盈的距离
                            distance_to_stop = (current_price / atr_stop_loss - 1) * 100
                            distance_to_profit = (atr_take_profit / current_price - 1) * 100

                            logger.info(f"      ✓ 未触发止损止盈")
                            logger.info(f"        距离ATR止损: {distance_to_stop:.1f}%")
                            logger.info(f"        距离ATR止盈: {distance_to_profit:.1f}%")

                    else:
                        logger.warning(f"      历史数据不足，无法计算ATR")

                except Exception as e:
                    logger.error(f"      计算止损止盈失败: {e}")

        logger.info("\n" + "=" * 70)
        logger.info("检查完成")
        logger.info("=" * 70)


if __name__ == "__main__":
    asyncio.run(check_positions())