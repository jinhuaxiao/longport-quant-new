#!/usr/bin/env python3
"""测试当前持仓的止损止盈设置"""

import asyncio
from datetime import datetime, timedelta
import numpy as np
from loguru import logger

# 添加项目根目录到路径
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from src.longport_quant.execution.client import LongportTradingClient
from src.longport_quant.data.quote_client import QuoteDataClient
from src.longport_quant.config import get_settings


class StopLossChecker:
    def __init__(self):
        self.settings = get_settings()
        self.execution = LongportTradingClient(self.settings)
        self.quote = QuoteDataClient(self.settings)

        # ATR动态止损参数
        self.atr_period = 14
        self.atr_stop_multiplier = 2.0  # 止损 = ATR × 2
        self.atr_profit_multiplier = 3.0  # 止盈 = ATR × 3

    async def check_positions(self):
        """检查所有持仓的止损止盈状态"""
        try:
            # 获取账户信息
            account_info = await self.execution.get_account_balance()
            positions = {}

            for currency in ['HKD', 'USD']:
                if currency in account_info:
                    for pos in account_info[currency].get('positions', []):
                        symbol = pos['symbol']
                        positions[symbol] = {
                            'quantity': pos['quantity'],
                            'cost': pos['cost'],
                            'current_price': pos['current_price'],
                            'pnl': pos['pnl'],
                            'pnl_pct': pos['pnl_pct']
                        }

            if not positions:
                logger.info("当前无持仓")
                return

            logger.info(f"发现 {len(positions)} 个持仓:")

            # 获取实时行情
            symbols = list(positions.keys())
            quotes = await self.quote.get_realtime_quotes(symbols)
            quote_dict = {q.symbol: float(q.last_done) for q in quotes}

            # 为每个持仓计算止损止盈
            for symbol, pos in positions.items():
                logger.info(f"\n{'='*60}")
                logger.info(f"标的: {symbol}")
                logger.info(f"  数量: {pos['quantity']}股")
                logger.info(f"  成本: ${pos['cost']:.2f}")

                current_price = quote_dict.get(symbol, pos['current_price'])
                logger.info(f"  当前价: ${current_price:.2f}")
                logger.info(f"  盈亏: ${pos['pnl']:.2f} ({pos['pnl_pct']:.2f}%)")

                # 尝试获取ATR并计算止损止盈
                try:
                    stop_loss, take_profit = await self.calculate_stops(symbol, pos['cost'])

                    if stop_loss and take_profit:
                        logger.info(f"  建议止损位: ${stop_loss:.2f} ({(stop_loss/pos['cost']-1)*100:.1f}%)")
                        logger.info(f"  建议止盈位: ${take_profit:.2f} ({(take_profit/pos['cost']-1)*100:.1f}%)")

                        # 检查是否触及止损止盈
                        if current_price <= stop_loss:
                            logger.warning(f"  ⚠️ 已触及止损位！应该卖出")
                        elif current_price >= take_profit:
                            logger.success(f"  ✅ 已触及止盈位！可以考虑卖出")
                        else:
                            distance_to_stop = (current_price - stop_loss) / stop_loss * 100
                            distance_to_profit = (take_profit - current_price) / current_price * 100
                            logger.info(f"  距离止损: {distance_to_stop:.1f}%")
                            logger.info(f"  距离止盈: {distance_to_profit:.1f}%")
                    else:
                        logger.info("  无法计算止损止盈（可能缺少历史数据）")

                except Exception as e:
                    logger.error(f"  计算止损止盈失败: {e}")

        except Exception as e:
            logger.error(f"检查持仓失败: {e}")
            import traceback
            traceback.print_exc()

    async def calculate_stops(self, symbol, entry_price):
        """计算止损止盈位"""
        try:
            # 获取最近的K线数据
            end_date = datetime.now()
            start_date = end_date - timedelta(days=30)

            klines = await self.quote.get_candlesticks(
                symbol=symbol,
                period='Day',
                count=30
            )

            if not klines or len(klines) < self.atr_period:
                logger.debug(f"{symbol}: K线数据不足，无法计算ATR")
                return None, None

            # 计算ATR
            highs = np.array([float(k.high) for k in klines[-self.atr_period:]])
            lows = np.array([float(k.low) for k in klines[-self.atr_period:]])
            closes = np.array([float(k.close) for k in klines[-self.atr_period:]])

            # TR = max(high-low, abs(high-prev_close), abs(low-prev_close))
            tr_values = []
            for i in range(1, len(highs)):
                hl = highs[i] - lows[i]
                hc = abs(highs[i] - closes[i-1])
                lc = abs(lows[i] - closes[i-1])
                tr = max(hl, hc, lc)
                tr_values.append(tr)

            atr = np.mean(tr_values)

            if np.isnan(atr):
                logger.debug(f"{symbol}: ATR计算结果为NaN")
                return None, None

            # 计算止损止盈
            stop_loss = entry_price - atr * self.atr_stop_multiplier
            take_profit = entry_price + atr * self.atr_profit_multiplier

            logger.debug(f"{symbol}: ATR=${atr:.2f}")

            return stop_loss, take_profit

        except Exception as e:
            logger.debug(f"{symbol}: 计算ATR失败 - {e}")
            return None, None


async def main():
    """主函数"""
    logger.info("="*80)
    logger.info("持仓止损止盈检查工具")
    logger.info("="*80)

    checker = StopLossChecker()
    await checker.check_positions()

    logger.info("\n检查完成")


if __name__ == "__main__":
    asyncio.run(main())