#!/usr/bin/env python3
"""测试止损止盈检查逻辑"""

import asyncio
from datetime import datetime
from loguru import logger
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient

async def check_positions_stops():
    """检查持仓的止损止盈设置情况"""

    settings = get_settings()
    quote_client = QuoteDataClient(settings)
    trade_client = LongportTradingClient(settings)

    logger.info("=" * 60)
    logger.info("检查账户持仓和止损止盈设置")
    logger.info("=" * 60)

    # 获取账户持仓
    positions_resp = await trade_client.stock_positions()

    positions = {}
    all_symbols = []

    for channel in positions_resp.channels:
        for pos in channel.positions:
            symbol = pos.symbol
            # 标准化港股代码
            if symbol.endswith('.HK'):
                code = symbol[:-3]
                if len(code) < 4 and code.isdigit():
                    symbol = f"{code.zfill(4)}.HK"

            all_symbols.append(symbol)
            positions[symbol] = {
                "quantity": pos.quantity,
                "cost": float(pos.cost_price) if pos.cost_price else 0,
                "currency": pos.currency
            }

    logger.info(f"\n发现 {len(positions)} 个持仓:")
    for symbol, pos in positions.items():
        logger.info(f"  {symbol}: {pos['quantity']}股 @ ${pos['cost']:.2f}")

    if not all_symbols:
        logger.warning("没有找到任何持仓")
        return

    # 获取实时行情
    logger.info(f"\n获取实时行情...")
    quotes = await quote_client.get_realtime_quote(all_symbols)

    logger.info(f"\n分析止损止盈状态:")
    logger.info("-" * 40)

    for quote in quotes:
        symbol = quote.symbol
        current_price = float(quote.last_done) if quote.last_done else 0

        if current_price <= 0:
            logger.warning(f"{symbol}: 无法获取当前价格")
            continue

        if symbol in positions:
            pos = positions[symbol]
            entry_price = pos['cost']

            if entry_price <= 0:
                logger.warning(f"{symbol}: 成本价为0，跳过")
                continue

            # 计算盈亏
            pnl_pct = (current_price / entry_price - 1) * 100

            # 简单的止损止盈计算（基于百分比）
            # 这里使用固定比例，实际脚本会用ATR计算
            stop_loss_pct = -5.0   # 5%止损
            take_profit_pct = 15.0  # 15%止盈

            stop_loss_price = entry_price * (1 + stop_loss_pct/100)
            take_profit_price = entry_price * (1 + take_profit_pct/100)

            logger.info(f"\n📊 {symbol}:")
            logger.info(f"   成本价: ${entry_price:.2f}")
            logger.info(f"   当前价: ${current_price:.2f}")
            logger.info(f"   盈亏: {pnl_pct:+.2f}%")
            logger.info(f"   止损位: ${stop_loss_price:.2f} (触发距离: {(current_price/stop_loss_price-1)*100:+.1f}%)")
            logger.info(f"   止盈位: ${take_profit_price:.2f} (触发距离: {(take_profit_price/current_price-1)*100:+.1f}%)")

            # 检查是否触发止损止盈
            if current_price <= stop_loss_price:
                logger.error(f"   🛑 已触发止损！应立即卖出")
            elif current_price >= take_profit_price:
                logger.success(f"   🎉 已触发止盈！可以考虑获利了结")
            elif pnl_pct < -3:
                logger.warning(f"   ⚠️ 接近止损位，需要密切关注")
            elif pnl_pct > 10:
                logger.info(f"   📈 盈利良好，接近止盈位")
            else:
                logger.info(f"   ✅ 正常持仓状态")

    logger.info("\n" + "=" * 60)
    logger.info("分析总结:")
    logger.info("=" * 60)

    # 统计需要关注的持仓
    risk_positions = []
    profit_positions = []

    for quote in quotes:
        symbol = quote.symbol
        if symbol in positions:
            current_price = float(quote.last_done) if quote.last_done else 0
            entry_price = positions[symbol]['cost']

            if current_price > 0 and entry_price > 0:
                pnl_pct = (current_price / entry_price - 1) * 100

                if pnl_pct < -5:
                    risk_positions.append((symbol, pnl_pct))
                elif pnl_pct > 15:
                    profit_positions.append((symbol, pnl_pct))

    if risk_positions:
        logger.warning(f"\n需要止损的持仓 ({len(risk_positions)}个):")
        for sym, pnl in risk_positions:
            logger.warning(f"  {sym}: {pnl:.1f}%")

    if profit_positions:
        logger.success(f"\n可以止盈的持仓 ({len(profit_positions)}个):")
        for sym, pnl in profit_positions:
            logger.success(f"  {sym}: +{pnl:.1f}%")

    if not risk_positions and not profit_positions:
        logger.info("\n所有持仓都在正常范围内")

if __name__ == "__main__":
    asyncio.run(check_positions_stops())