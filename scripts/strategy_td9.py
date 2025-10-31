#!/usr/bin/env python3
"""
TD Sequential（TD9 简化版）策略 - 日线耗竭/反转提示（只做多侧 Buy Setup）

规则（简化）：
- Buy Setup：连续9根K线满足 close[t] < close[t-4]
- 完成第9根时生成 BUY 信号；可选“perfected”条件：第8/9根的最低价 < 第6/7根最低价

说明：
- 本脚本只产出信号，统一由 OrderExecutor 执行与风控。
- 生产环境建议与趋势/ATR/量能做合并过滤以提升质量。
"""

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo
from loguru import logger
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.messaging import SignalQueue


@dataclass
class TD9Params:
    perfected: bool = True  # 是否要求perfected确认
    budget_pct: float = 0.10
    score: int = 55


class TD9Strategy:
    def __init__(self, account_id: Optional[str] = None, perfected: bool = True, budget_pct: float = 0.10, score: int = 55) -> None:
        self.settings = get_settings(account_id=account_id)
        self.beijing_tz = ZoneInfo(self.settings.timezone)
        self.params = TD9Params(perfected=perfected, budget_pct=float(budget_pct), score=int(score))

        self.signal_queue = SignalQueue(
            redis_url=self.settings.redis_url,
            queue_key=self.settings.signal_queue_key,
            processing_key=self.settings.signal_processing_key,
            failed_key=self.settings.signal_failed_key,
            max_retries=self.settings.signal_max_retries,
        )

        # 防止同一交易日重复发信号
        self.triggered_day = {}

    def _compute_buy_setup(self, closes: List[float]) -> List[int]:
        """返回每根K线的 Buy Setup 连续计数。"""
        n = len(closes)
        cnt = [0] * n
        for i in range(n):
            if i >= 4 and closes[i] < closes[i - 4]:
                cnt[i] = (cnt[i - 1] + 1) if i > 0 else 1
            else:
                cnt[i] = 0
        return cnt

    def _is_perfected_buy(self, lows: List[float], idx: int) -> bool:
        """简单perfected判定：第8或第9根低点 < 第6/7根低点。"""
        if idx < 8:
            return False
        low8 = lows[idx - 1]
        low9 = lows[idx]
        ref = min(lows[idx - 3], lows[idx - 2])  # bars 6/7 -> idx-3, idx-2
        return (low8 < ref) or (low9 < ref)

    async def run(self) -> None:
        logger.info("=" * 70)
        logger.info(f"🚀 启动 TD9 策略: perfected={self.params.perfected}, 预算={self.params.budget_pct*100:.1f}%")
        logger.info("=" * 70)

        wl = WatchlistLoader().load()
        symbols = wl.symbols()
        if not symbols:
            logger.warning("监控列表为空，退出")
            return

        try:
            while True:
                now = datetime.now(self.beijing_tz)
                start = now - timedelta(days=120)

                async with QuoteDataClient(self.settings) as quote_client:
                    for symbol in symbols:
                        try:
                            klines = await quote_client.get_history_candles(
                                symbol=symbol,
                                period=openapi.Period.Day,
                                adjust_type=openapi.AdjustType.NoAdjust,
                                start=start,
                                end=now,
                            )
                            if not klines or len(klines) < 30:
                                continue

                            closes = [float(k.close) for k in klines]
                            lows = [float(k.low) for k in klines]
                            highs = [float(k.high) for k in klines]

                            cnt = self._compute_buy_setup(closes)
                            idx = len(closes) - 1

                            # 今天是否已触发
                            day_key = now.date()
                            if (symbol, day_key) in self.triggered_day:
                                continue

                            just_completed = cnt[idx] >= 9 and cnt[idx - 1] == 8
                            if not just_completed:
                                continue

                            if self.params.perfected and not self._is_perfected_buy(lows, idx):
                                # 非perfected，跳过
                                continue

                            # 构造信号（止损用近 N 日低点；此处选最近9根最低）
                            recent_low = min(lows[max(0, idx - 8): idx + 1])
                            last_price = closes[idx]
                            signal = {
                                'symbol': symbol,
                                'type': 'BUY',
                                'side': 'BUY',
                                'score': self.params.score,
                                'price': last_price,
                                'reasons': [
                                    'TD Buy Setup 完成9',
                                    'Perfected' if self.params.perfected else 'Non-perfected'
                                ],
                                'strategy': 'TD9',
                                'budget_pct': self.params.budget_pct,
                                'stop_loss': round(recent_low, 4),
                            }
                            ok = await self.signal_queue.publish_signal(signal)
                            if ok:
                                self.triggered_day[(symbol, day_key)] = True
                                logger.success(f"[{symbol}] ✅ TD9 Buy Setup 完成9，价格={last_price:.3f}")

                        except Exception as e:
                            logger.debug(f"{symbol} TD9 计算失败: {e}")

                # 日线策略：10分钟轮询一次已足够（可改盘后批处理）
                await asyncio.sleep(600)
        finally:
            await self.signal_queue.close()


async def main():
    import argparse

    parser = argparse.ArgumentParser(description='TD9 策略信号生成器（Buy Setup 简化版）')
    parser.add_argument('--account-id', dest='account_id', default=None, help='账号ID（可选）')
    parser.add_argument('--no-perfected', dest='no_perfected', action='store_true', help='不要求perfected')
    parser.add_argument('--budget-pct', dest='budget_pct', type=float, default=0.10, help='预算占比（默认10%）')
    parser.add_argument('--score', dest='score', type=int, default=55, help='信号评分（默认55）')
    args = parser.parse_args()

    strat = TD9Strategy(
        account_id=args.account_id,
        perfected=not args.no_perfected,
        budget_pct=args.budget_pct,
        score=args.score,
    )
    await strat.run()


if __name__ == '__main__':
    asyncio.run(main())
