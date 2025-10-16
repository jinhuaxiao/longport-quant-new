#!/usr/bin/env python3
"""检查中芯国际的止损状态"""

import asyncio
from datetime import datetime, timedelta
import numpy as np
from loguru import logger
from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient
from longport_quant.features.technical_indicators import TechnicalIndicators

async def check_smic_stop_loss():
    """专门检查中芯国际的止损状态"""

    settings = get_settings()
    quote_client = QuoteDataClient(settings)
    trade_client = LongportTradingClient(settings)

    # 检查多种可能的股票代码格式
    symbols = ["981.HK", "0981.HK"]

    logger.info("=" * 60)
    logger.info("🔍 中芯国际止损状态分析")
    logger.info("=" * 60)

    # 获取账户持仓
    positions_resp = await trade_client.stock_positions()

    # 查找中芯国际的持仓
    smic_position = None
    actual_symbol = None

    for channel in positions_resp.channels:
        for pos in channel.positions:
            if pos.symbol in symbols or pos.symbol.startswith("981") or pos.symbol.startswith("0981"):
                smic_position = {
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "cost": float(pos.cost_price) if pos.cost_price else 0,
                    "currency": pos.currency
                }
                actual_symbol = pos.symbol
                logger.info(f"\n✅ 找到中芯国际持仓:")
                logger.info(f"   股票代码: {pos.symbol}")
                logger.info(f"   持仓数量: {pos.quantity}股")
                logger.info(f"   成本价: ${smic_position['cost']:.2f}")
                break

    if not smic_position:
        logger.warning("❌ 未找到中芯国际的持仓")
        return

    # 获取实时行情（尝试多种代码格式）
    current_price = None
    for symbol in [actual_symbol, "981.HK", "0981.HK"]:
        try:
            quotes = await quote_client.get_realtime_quote([symbol])
            if quotes and len(quotes) > 0:
                current_price = float(quotes[0].last_done) if quotes[0].last_done else 0
                if current_price > 0:
                    logger.info(f"\n📊 实时行情 ({symbol}):")
                    logger.info(f"   当前价格: ${current_price:.2f}")
                    break
        except Exception as e:
            logger.debug(f"   尝试 {symbol} 失败: {e}")

    if not current_price:
        logger.error("❌ 无法获取实时价格")
        return

    # 计算盈亏
    entry_price = smic_position['cost']
    pnl = current_price - entry_price
    pnl_pct = (current_price / entry_price - 1) * 100

    logger.info(f"\n💰 盈亏分析:")
    logger.info(f"   盈亏金额: ${pnl:.2f}")
    logger.info(f"   盈亏比例: {pnl_pct:+.2f}%")

    if pnl_pct < 0:
        logger.warning(f"   📉 当前亏损 {abs(pnl_pct):.2f}%")
    else:
        logger.success(f"   📈 当前盈利 {pnl_pct:.2f}%")

    # 尝试计算ATR和动态止损位
    logger.info(f"\n🎯 计算止损位:")

    try:
        # 使用标准化的股票代码
        symbol_for_history = "0981.HK"

        end_date = datetime.now()
        start_date = end_date - timedelta(days=60)

        logger.info(f"   获取历史数据 ({symbol_for_history})...")
        candles = await quote_client.get_history_candles(
            symbol=symbol_for_history,
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

            if not np.isnan(current_atr):
                # 计算动态止损止盈位
                atr_stop_multiplier = 2.0
                atr_profit_multiplier = 3.0

                stop_loss_atr = entry_price - current_atr * atr_stop_multiplier
                take_profit_atr = entry_price + current_atr * atr_profit_multiplier

                logger.info(f"\n📐 基于ATR的止损止盈位:")
                logger.info(f"   ATR(14): ${current_atr:.2f}")
                logger.info(f"   动态止损位: ${stop_loss_atr:.2f} (成本 - ATR×2)")
                logger.info(f"   动态止盈位: ${take_profit_atr:.2f} (成本 + ATR×3)")

                # 检查是否触发止损
                if current_price <= stop_loss_atr:
                    logger.error(f"\n🛑 **已触发ATR止损！**")
                    logger.error(f"   当前价 ${current_price:.2f} <= 止损位 ${stop_loss_atr:.2f}")
                    logger.error(f"   建议立即卖出止损")
                else:
                    distance_to_stop = (current_price - stop_loss_atr) / current_price * 100
                    logger.info(f"\n✅ 未触发ATR止损")
                    logger.info(f"   距离止损位还有 {distance_to_stop:.1f}%")
            else:
                logger.warning("   ATR计算结果为NaN")
        else:
            logger.warning(f"   历史数据不足（需要14天，实际{len(candles) if candles else 0}天）")

    except Exception as e:
        logger.error(f"   计算ATR止损位失败: {e}")

    # 使用固定百分比止损
    logger.info(f"\n📏 固定百分比止损分析:")

    # 不同的止损百分比
    stop_loss_levels = [5, 7, 10, 15]

    for level in stop_loss_levels:
        stop_price = entry_price * (1 - level/100)
        if current_price <= stop_price:
            logger.error(f"   ❌ {level}%止损: ${stop_price:.2f} - **已触发**")
        else:
            distance = (current_price - stop_price) / current_price * 100
            status = "⚠️ 接近" if distance < 2 else "✅ 安全"
            logger.info(f"   {status} {level}%止损: ${stop_price:.2f} (距离 {distance:.1f}%)")

    # 分析可能的原因
    logger.info(f"\n🔍 可能没有自动止损的原因:")
    logger.info("1. 脚本重启后丢失了内存中的止损设置")
    logger.info("2. 首次运行时未能成功获取历史数据计算ATR")
    logger.info("3. 股票代码格式不匹配（981.HK vs 0981.HK）")
    logger.info("4. 手动买入的持仓，系统没有记录入场价和止损位")
    logger.info("5. 止损位设置过宽（ATR×2可能给了较大的下跌空间）")

    # 建议
    logger.info(f"\n💡 建议:")

    if pnl_pct < -10:
        logger.error("⚠️ 亏损已超过10%，建议立即评估是否需要手动止损")
    elif pnl_pct < -5:
        logger.warning("⚠️ 亏损接近5%，需要密切关注")

    logger.info("1. 运行 advanced_technical_trading.py 时会自动设置止损")
    logger.info("2. 考虑手动设置一个固定止损位（如5%或7%）")
    logger.info("3. 确保脚本持续运行以监控止损")

    # 计算如果现在卖出的损失
    if pnl_pct < 0:
        loss_amount = abs(pnl) * smic_position['quantity']
        logger.info(f"\n📊 如果现在卖出:")
        logger.info(f"   总亏损金额: ${loss_amount:.2f}")
        logger.info(f"   每股亏损: ${abs(pnl):.2f}")

if __name__ == "__main__":
    asyncio.run(check_smic_stop_loss())