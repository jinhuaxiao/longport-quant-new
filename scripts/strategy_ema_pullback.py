#!/usr/bin/env python3
"""
EMA 回撤上车（20/50EMA + 趋势过滤）

逻辑：
- 趋势过滤：EMA20 > EMA50 且（可选）RSI14 > 50
- 回撤条件：前一日收盘 < EMA20，今日收盘上穿 EMA20（或距 EMA20 不超过 tol% 且收阳）
- 触发后发 BUY 信号，止损可放在 EMA50 或最近 swing low（此处用 EMA50 兜底）

参数：
- --rsi-filter 是否使用 RSI>50 过滤
- --tol-percent 与 EMA20 的容差（默认 0.5%）
"""

import asyncio
import sys
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from loguru import logger
from pathlib import Path
import numpy as np

sys.path.append(str(Path(__file__).parent.parent))

from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.messaging import SignalQueue
from longport_quant.features.technical_indicators import TechnicalIndicators


class EMAPullbackStrategy:
    def __init__(
        self,
        account_id: Optional[str] = None,
        use_rsi_filter: bool = True,
        tol_percent: float = 0.005,
        budget_pct: float = 0.10,
        score: int = 60,
        poll_minutes: int = 10,
    ) -> None:
        self.settings = get_settings(account_id=account_id)
        self.tz = ZoneInfo(self.settings.timezone)
        self.use_rsi_filter = bool(use_rsi_filter)
        self.tol = float(tol_percent)
        self.budget_pct = float(budget_pct)
        self.score = int(score)
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
        logger.info(
            f"🚀 启动 EMA Pullback 策略：容差={self.tol*100:.2f}%, 预算={self.budget_pct*100:.1f}%, RSI过滤={self.use_rsi_filter}"
        )
        logger.info("=" * 70)

        wl = WatchlistLoader().load()
        symbols = wl.symbols()
        if not symbols:
            logger.warning("监控列表为空，退出")
            return

        try:
            while True:
                now = datetime.now(self.tz)
                start = now - timedelta(days=200)

                async with QuoteDataClient(self.settings) as qc:
                    for symbol in symbols:
                        try:
                            candles = await qc.get_history_candles(
                                symbol=symbol,
                                period=openapi.Period.Day,
                                adjust_type=openapi.AdjustType.NoAdjust,
                                start=start,
                                end=now,
                            )
                            if not candles or len(candles) < 60:
                                continue

                            closes = np.array([float(k.close) for k in candles], dtype=float)
                            highs = np.array([float(k.high) for k in candles], dtype=float)
                            lows = np.array([float(k.low) for k in candles], dtype=float)

                            ema20 = TechnicalIndicators.ema(closes, 20)
                            ema50 = TechnicalIndicators.ema(closes, 50)
                            rsi14 = TechnicalIndicators.rsi(closes, 14)

                            i = len(closes) - 1
                            # 趋势过滤
                            if np.isnan(ema20[i]) or np.isnan(ema50[i]) or ema20[i] <= ema50[i]:
                                continue

                            if self.use_rsi_filter and (np.isnan(rsi14[i]) or rsi14[i] <= 50):
                                continue

                            # 回撤+上穿 EMA20
                            prev_close = closes[i - 1]
                            curr_close = closes[i]
                            e20_prev = ema20[i - 1]
                            e20_curr = ema20[i]

                            # 条件：上一日低于EMA20，今日收盘 >= EMA20 或接近（容差）且收阳
                            was_below = prev_close < e20_prev
                            crossed_up = (curr_close >= e20_curr) or (abs(curr_close - e20_curr) / e20_curr <= self.tol and curr_close > prev_close)

                            if not (was_below and crossed_up):
                                continue

                            # 当日仅一次
                            key = (symbol, now.date())
                            if key in self.triggered_day:
                                continue

                            stop_loss = float(ema50[i]) if not np.isnan(ema50[i]) else float(min(lows[-20:]))

                            signal = {
                                'symbol': symbol,
                                'type': 'BUY',
                                'side': 'BUY',
                                'score': self.score,
                                'price': float(curr_close),
                                'reasons': [
                                    'EMA20>EMA50 上升趋势',
                                    '回撤后上穿EMA20',
                                    'RSI>50' if self.use_rsi_filter else 'RSI未启用'
                                ],
                                'strategy': 'EMA_PB',
                                'budget_pct': self.budget_pct,
                                'stop_loss': round(stop_loss, 4),
                            }

                            ok = await self.signal_queue.publish_signal(signal)
                            if ok:
                                self.triggered_day[key] = True
                                logger.success(f"[{symbol}] ✅ EMA Pullback 信号：close={curr_close:.3f} 上穿 EMA20={e20_curr:.3f}")

                        except Exception as e:
                            logger.debug(f"{symbol} EMA Pullback 计算失败: {e}")

                await asyncio.sleep(self.poll_minutes * 60)
        finally:
            await self.signal_queue.close()


async def main():
    import argparse
    p = argparse.ArgumentParser(description='EMA Pullback 策略信号生成器')
    p.add_argument('--account-id', dest='account_id', default=None)
    p.add_argument('--no-rsi', dest='no_rsi', action='store_true')
    p.add_argument('--tol-percent', dest='tol', type=float, default=0.005)
    p.add_argument('--budget-pct', dest='budget_pct', type=float, default=0.10)
    p.add_argument('--score', dest='score', type=int, default=60)
    p.add_argument('--interval-min', dest='interval', type=int, default=10)
    args = p.parse_args()

    strat = EMAPullbackStrategy(
        account_id=args.account_id,
        use_rsi_filter=not args.no_rsi,
        tol_percent=args.tol,
        budget_pct=args.budget_pct,
        score=args.score,
        poll_minutes=args.interval,
    )
    await strat.run()


if __name__ == '__main__':
    asyncio.run(main())
