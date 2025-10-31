#!/usr/bin/env python3
"""
Gap 策略（简化：Gap-and-Go 多头）

逻辑：
- 在市场开盘常规时段，若开盘价相对前收涨幅 >= gap_pct，且现价相对开盘价继续上行 >= cont_pct，则生成 BUY 信号
- 美股/港股按本地时区窗口判断，仅常规时段内有效

备注：
- 只做多侧（Gap-and-Go）；做空/回补需考虑 SSR/融券限制，这里不实现
"""

import asyncio
import sys
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo
from loguru import logger
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.messaging import SignalQueue


class GapGoStrategy:
    def __init__(
        self,
        account_id: Optional[str] = None,
        gap_pct: float = 0.03,
        cont_pct: float = 0.005,
        budget_pct: float = 0.10,
        score: int = 60,
        poll_seconds: float = 5.0,
    ) -> None:
        self.settings = get_settings(account_id=account_id)
        self.tz = ZoneInfo(self.settings.timezone)
        self.gap_pct = float(gap_pct)
        self.cont_pct = float(cont_pct)
        self.budget_pct = float(budget_pct)
        self.score = int(score)
        self.poll_seconds = float(poll_seconds)

        self.signal_queue = SignalQueue(
            redis_url=self.settings.redis_url,
            queue_key=self.settings.signal_queue_key,
            processing_key=self.settings.signal_processing_key,
            failed_key=self.settings.signal_failed_key,
            max_retries=self.settings.signal_max_retries,
        )

        # 当日每票仅触发一次
        self.triggered = set()

    def _in_regular_session(self, symbol: str, now: datetime) -> bool:
        t = now.time()
        wd = now.weekday()
        if wd >= 5:
            return False
        if symbol.endswith('.US'):
            # 本地时区下美股常规时段 21:30-04:00（简化覆盖夏令/冬令）
            return (t >= time(21, 30)) or (t <= time(4, 0))
        if symbol.endswith('.HK'):
            return (time(9, 30) <= t <= time(12, 0)) or (time(13, 0) <= t <= time(16, 0))
        return True

    async def run(self) -> None:
        logger.info("=" * 70)
        logger.info(
            f"🚀 启动 GAP-Go 策略：gap≥{self.gap_pct*100:.1f}%, 延续≥{self.cont_pct*100:.2f}%, 预算={self.budget_pct*100:.1f}%"
        )
        logger.info("=" * 70)

        wl = WatchlistLoader().load()
        symbols = wl.symbols()
        if not symbols:
            logger.warning("监控列表为空，退出")
            return

        async with QuoteDataClient(self.settings) as qc:
            try:
                while True:
                    now = datetime.now(self.tz)
                    quotes = await qc.get_realtime_quote(symbols)
                    for q in quotes or []:
                        try:
                            symbol = q.symbol
                            if not self._in_regular_session(symbol, now):
                                continue

                            # 今日已触发过则跳过
                            key = (symbol, now.date())
                            if key in self.triggered:
                                continue

                            last = float(q.last_done) if q.last_done else 0.0
                            prev_close = float(q.prev_close) if q.prev_close else 0.0
                            open_px = float(q.open) if q.open else 0.0
                            if last <= 0 or prev_close <= 0 or open_px <= 0:
                                continue

                            gap = (open_px - prev_close) / prev_close
                            cont = (last - open_px) / open_px

                            if gap >= self.gap_pct and cont >= self.cont_pct:
                                signal = {
                                    'symbol': symbol,
                                    'type': 'BUY',
                                    'side': 'BUY',
                                    'score': self.score,
                                    'price': last,
                                    'reasons': [
                                        f'Gap {gap*100:.1f}%',
                                        f'延续 {cont*100:.2f}%'
                                    ],
                                    'strategy': 'GAP_GO',
                                    'budget_pct': self.budget_pct,
                                }
                                ok = await self.signal_queue.publish_signal(signal)
                                if ok:
                                    self.triggered.add(key)
                                    logger.success(
                                        f"[{symbol}] ✅ GAP-Go: open={open_px:.3f} prev={prev_close:.3f} last={last:.3f}"
                                    )
                        except Exception as e:
                            logger.debug(f"{q.symbol} GAP计算失败: {e}")

                    await asyncio.sleep(self.poll_seconds)
            finally:
                await self.signal_queue.close()


async def main():
    import argparse
    p = argparse.ArgumentParser(description='GAP-Go 策略信号生成器（多头）')
    p.add_argument('--account-id', dest='account_id', default=None)
    p.add_argument('--gap-pct', dest='gap_pct', type=float, default=0.03)
    p.add_argument('--cont-pct', dest='cont_pct', type=float, default=0.005)
    p.add_argument('--budget-pct', dest='budget_pct', type=float, default=0.10)
    p.add_argument('--score', dest='score', type=int, default=60)
    p.add_argument('--interval', dest='interval', type=float, default=5.0)
    args = p.parse_args()

    strat = GapGoStrategy(
        account_id=args.account_id,
        gap_pct=args.gap_pct,
        cont_pct=args.cont_pct,
        budget_pct=args.budget_pct,
        score=args.score,
        poll_seconds=args.interval,
    )
    await strat.run()


if __name__ == '__main__':
    asyncio.run(main())

