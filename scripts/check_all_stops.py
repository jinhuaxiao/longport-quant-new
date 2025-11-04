#!/usr/bin/env python3
"""检查所有持仓的止损设置"""

import asyncio
from longport_quant.config import get_settings
from longport_quant.persistence.stop_manager import StopLossManager
from longport_quant.execution.client import LongportTradingClient
from longport_quant.data.quote_client import QuoteDataClient
from loguru import logger

async def check_all_stops():
    """检查所有持仓的止损设置"""
    settings = get_settings(account_id="live_001")

    stop_manager = StopLossManager()
    await stop_manager.connect()

    # 获取所有活跃的止损记录
    all_stops = await stop_manager.load_active_stops()

    if not all_stops:
        logger.warning("⚠️ 数据库中没有活跃的止损记录")
        return

    # 获取当前持仓和价格
    async with LongportTradingClient(settings) as trade_client, \
               QuoteDataClient(settings) as quote_client:

        # 获取持仓
        positions = await trade_client.stock_positions()
        position_map = {p.symbol: int(p.quantity) for p in positions.channels[0].positions if hasattr(positions, 'channels') and positions.channels}

        # 获取实时价格
        symbols = list(all_stops.keys())
        quotes = await quote_client.get_realtime_quote(symbols) if symbols else []
        price_map = {q.symbol: float(q.last_done) for q in quotes}

        logger.info("=" * 80)
        logger.info("所有持仓的止损设置汇总")
        logger.info("=" * 80)

        for symbol, stop_info in all_stops.items():
            entry_price = stop_info['entry_price']
            stop_loss = stop_info['stop_loss']
            take_profit = stop_info['take_profit']
            quantity = stop_info['quantity']

            current_price = price_map.get(symbol, 0)
            current_qty = position_map.get(symbol, 0)

            # 计算盈亏
            if current_price > 0:
                pnl_pct = (current_price - entry_price) / entry_price * 100
                stop_loss_pct = (stop_loss - entry_price) / entry_price * 100 if stop_loss else None
                take_profit_pct = (take_profit - entry_price) / entry_price * 100 if take_profit else None

                # 判断状态
                if current_price <= stop_loss:
                    status = "🔴 应该触发止损！"
                elif take_profit and current_price >= take_profit:
                    status = "🟢 应该触发止盈！"
                else:
                    status = "⚪ 正常范围"

                # 格式化止盈价格显示
                if take_profit:
                    tp_display = f"${take_profit:.2f}  ({take_profit_pct:+.2f}%)"
                else:
                    tp_display = "未设置"

                logger.info(f"""
{symbol}  {status}
  入场价: ${entry_price:.2f}
  当前价: ${current_price:.2f}  ({pnl_pct:+.2f}%)
  止损价: ${stop_loss:.2f}  ({stop_loss_pct:.2f}%)
  止盈价: {tp_display}
  数量: {quantity}股 (当前持仓: {current_qty}股)
""")
            else:
                logger.warning(f"""
{symbol}  ⚠️ 无法获取价格
  入场价: ${entry_price:.2f}
  止损价: ${stop_loss:.2f}
  数量: {quantity}股 (当前持仓: {current_qty}股)
""")

        logger.info("=" * 80)
        logger.info(f"总计: {len(all_stops)} 个持仓有止损记录")
        logger.info("=" * 80)

if __name__ == "__main__":
    asyncio.run(check_all_stops())
