#!/usr/bin/env python3
"""
VWAP/AVWAP 日内策略（简化版）

逻辑（默认 Anchored to 开盘）：
- 以当日分时线累计 (price * volume) / volume 计算 Anchored VWAP
- 价格上穿且偏离超阈值 dev_pct 时，生成顺势 BUY 信号
- 只做做多入场（卖出由统一风控/止损托管）

信号字段：strategy='VWAP'，可携带 budget_pct / budget_notional
"""

import asyncio
import sys
from datetime import datetime, timedelta
from typing import Dict, Optional
from zoneinfo import ZoneInfo
from loguru import logger
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.messaging import SignalQueue


class VWAPStrategy:
    def __init__(
        self,
        account_id: Optional[str] = None,
        dev_pct: float = 0.005,
        budget_pct: float = 0.10,
        score: int = 60,
        poll_interval: float = 15.0,
    ) -> None:
        self.settings = get_settings(account_id=account_id)
        self.beijing_tz = ZoneInfo(self.settings.timezone)
        self.dev_pct = float(dev_pct)
        self.budget_pct = float(budget_pct)
        self.base_score = int(score)
        self.poll_interval = float(poll_interval)

        self.signal_queue = SignalQueue(
            redis_url=self.settings.redis_url,
            queue_key=self.settings.signal_queue_key,
            processing_key=self.settings.signal_processing_key,
            failed_key=self.settings.signal_failed_key,
            max_retries=self.settings.signal_max_retries,
        )

        # 当日状态：是否已触发（防重复）
        self.triggered: Dict[str, datetime.date] = {}

    async def run(self) -> None:
        logger.info("=" * 70)
        logger.info(
            f"🚀 启动 VWAP 策略：偏离阈值={self.dev_pct*100:.2f}%, 预算={self.budget_pct*100:.1f}%"
        )
        logger.info("=" * 70)

        wl = WatchlistLoader().load()
        symbols = wl.symbols()
        if not symbols:
            logger.warning("监控列表为空，退出")
            return

        async with QuoteDataClient(self.settings) as quote_client:
            try:
                while True:
                    now = datetime.now(self.beijing_tz)

                    for symbol in symbols:
                        try:
                            intra = await quote_client.get_intraday(symbol)
                            if not intra or not intra.price or not intra.volume:
                                continue

                            prices = list(map(float, intra.price))
                            vols = list(map(float, intra.volume))
                            if not prices or not vols:
                                continue

                            cur_price = prices[-1]
                            total_vol = sum(vols)
                            if total_vol <= 0:
                                continue

                            # 计算锚定VWAP（从当日开始）
                            vwap = sum(p * v for p, v in zip(prices, vols)) / total_vol
                            dev = (cur_price - vwap) / vwap

                            # 跨日重置
                            if symbol in self.triggered and self.triggered[symbol] != now.date():
                                del self.triggered[symbol]

                            # 向上突破且未触发过
                            if dev >= self.dev_pct and symbol not in self.triggered:
                                signal = {
                                    'symbol': symbol,
                                    'type': 'BUY',
                                    'side': 'BUY',
                                    'score': self.base_score,
                                    'price': cur_price,
                                    'reasons': [f'价格高于VWAP {dev*100:.2f}%'],
                                    'strategy': 'VWAP',
                                    'budget_pct': self.budget_pct,
                                }
                                ok = await self.signal_queue.publish_signal(signal)
                                if ok:
                                    self.triggered[symbol] = now.date()
                                    logger.success(
                                        f"[{symbol}] ✅ VWAP 信号：price={cur_price:.3f} > VWAP={vwap:.3f} (+{dev*100:.2f}%)"
                                    )
                        except Exception as e:
                            logger.debug(f"{symbol} VWAP 计算失败: {e}")

                    await asyncio.sleep(self.poll_interval)
            finally:
                await self.signal_queue.close()


async def main():
    import argparse

    parser = argparse.ArgumentParser(description='VWAP 策略信号生成器')
    parser.add_argument('--account-id', dest='account_id', default=None, help='账号ID（可选）')
    parser.add_argument('--dev-pct', dest='dev_pct', type=float, default=0.005, help='VWAP 偏离阈值（默认0.005=0.5%）')
    parser.add_argument('--budget-pct', dest='budget_pct', type=float, default=0.10, help='预算占比（默认10%）')
    parser.add_argument('--score', dest='score', type=int, default=60, help='信号评分（默认60）')
    parser.add_argument('--interval', dest='interval', type=float, default=15.0, help='轮询间隔秒（默认15）')
    args = parser.parse_args()

    strat = VWAPStrategy(
        account_id=args.account_id,
        dev_pct=args.dev_pct,
        budget_pct=args.budget_pct,
        score=args.score,
        poll_interval=args.interval,
    )
    await strat.run()


if __name__ == '__main__':
    asyncio.run(main())

