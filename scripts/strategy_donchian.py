#!/usr/bin/env python3
"""
唐奇安通道（Donchian / 海龟）突破策略（日线）

逻辑：
- 收盘/现价突破过去 N 日最高价则做多；（仅多头）
- 止损可设置为 M 日低点（随信号一起下发，执行端托管）

信号字段：strategy='DONCHIAN'，可携带 budget_pct / budget_notional
"""

import asyncio
import sys
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from loguru import logger
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.messaging import SignalQueue


class DonchianStrategy:
    def __init__(
        self,
        account_id: Optional[str] = None,
        n: int = 20,
        m: int = 10,
        budget_pct: float = 0.10,
        score: int = 60,
        poll_minutes: int = 10,
    ) -> None:
        self.settings = get_settings(account_id=account_id)
        self.beijing_tz = ZoneInfo(self.settings.timezone)
        self.n = int(n)
        self.m = int(m)
        self.budget_pct = float(budget_pct)
        self.base_score = int(score)
        self.poll_minutes = int(poll_minutes)

        self.signal_queue = SignalQueue(
            redis_url=self.settings.redis_url,
            queue_key=self.settings.signal_queue_key,
            processing_key=self.settings.signal_processing_key,
            failed_key=self.settings.signal_failed_key,
            max_retries=self.settings.signal_max_retries,
        )

        self.triggered_day = {}

    async def run(self) -> None:
        logger.info("=" * 70)
        logger.info(f"🚀 启动 Donchian 策略：N={self.n}, M={self.m}, 预算={self.budget_pct*100:.1f}%")
        logger.info("=" * 70)

        wl = WatchlistLoader().load()
        symbols = wl.symbols()
        if not symbols:
            logger.warning("监控列表为空，退出")
            return

        try:
            while True:
                now = datetime.now(self.beijing_tz)
                start = now - timedelta(days=max(60, self.n + self.m + 10))

                # 按需创建并立即释放QuoteContext，避免长期占用连接
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
                            if not klines or len(klines) < self.n + 2:
                                continue

                            closes = [float(k.close) for k in klines]
                            highs = [float(k.high) for k in klines]
                            lows = [float(k.low) for k in klines]

                            last_price = closes[-1]
                            n_high = max(highs[-(self.n + 1):-1])  # 不含当前K
                            m_low = min(lows[-(self.m + 1):-1]) if self.m > 0 else None

                            day_key = now.date()
                            if (symbol, day_key) in self.triggered_day:
                                continue

                            if last_price > n_high:
                                signal = {
                                    'symbol': symbol,
                                    'type': 'BUY',
                                    'side': 'BUY',
                                    'score': self.base_score,
                                    'price': last_price,
                                    'reasons': [f'{self.n}日通道突破({n_high:.2f})'],
                                    'strategy': 'DONCHIAN',
                                    'budget_pct': self.budget_pct,
                                }
                                if m_low:
                                    signal['stop_loss'] = m_low

                                ok = await self.signal_queue.publish_signal(signal)
                                if ok:
                                    self.triggered_day[(symbol, day_key)] = True
                                    logger.success(
                                        f"[{symbol}] ✅ Donchian 信号：price={last_price:.3f} > N_high={n_high:.3f}"
                                    )
                        except Exception as e:
                            logger.debug(f"{symbol} Donchian 计算失败: {e}")

                await asyncio.sleep(self.poll_minutes * 60)
        finally:
            await self.signal_queue.close()


async def main():
    import argparse

    parser = argparse.ArgumentParser(description='Donchian 策略信号生成器')
    parser.add_argument('--account-id', dest='account_id', default=None, help='账号ID（可选）')
    parser.add_argument('--n', dest='n', type=int, default=20, help='通道周期N（默认20）')
    parser.add_argument('--m', dest='m', type=int, default=10, help='止损周期M（默认10）')
    parser.add_argument('--budget-pct', dest='budget_pct', type=float, default=0.10, help='预算占比（默认10%）')
    parser.add_argument('--score', dest='score', type=int, default=60, help='信号评分（默认60）')
    parser.add_argument('--interval-min', dest='interval', type=int, default=10, help='轮询间隔分钟（默认10）')
    args = parser.parse_args()

    strat = DonchianStrategy(
        account_id=args.account_id,
        n=args.n,
        m=args.m,
        budget_pct=args.budget_pct,
        score=args.score,
        poll_minutes=args.interval,
    )
    await strat.run()


if __name__ == '__main__':
    asyncio.run(main())
