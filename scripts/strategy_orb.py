#!/usr/bin/env python3
"""
开盘区间突破（ORB）策略生成器

思路：
- 记录开盘后前 N 分钟的最高/最低价作为开盘区间（Opening Range）
- 当价格向上有效突破区间高点时，生成 BUY 信号（仅多头）
- 信号通过 Redis 队列下发，统一由 OrderExecutor 执行

注意：
- 本脚本只负责“产出信号”，不直接下单
- 资金与风控由执行端统一处理；可在信号里传入预算覆盖默认预算
"""

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo
from loguru import logger
from pathlib import Path

# 路径与依赖
sys.path.append(str(Path(__file__).parent.parent))

from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.messaging import SignalQueue


@dataclass
class ORBState:
    day: datetime.date
    window_end: datetime
    high: float
    low: float
    armed: bool  # 是否已完成区间并开始等待突破
    triggered: bool  # 是否今日已触发


class ORBStrategy:
    def __init__(
        self,
        account_id: Optional[str] = None,
        window_minutes: int = 15,
        budget_pct: float = 0.10,
        score: int = 60,
    ) -> None:
        self.settings = get_settings(account_id=account_id)
        self.beijing_tz = ZoneInfo(self.settings.timezone)
        self.window_minutes = max(1, int(window_minutes))
        self.budget_pct = float(budget_pct)
        self.base_score = int(score)

        self.signal_queue = SignalQueue(
            redis_url=self.settings.redis_url,
            queue_key=self.settings.signal_queue_key,
            processing_key=self.settings.signal_processing_key,
            failed_key=self.settings.signal_failed_key,
            max_retries=self.settings.signal_max_retries,
        )

        # 每个标的的当日状态
        self.state: Dict[str, ORBState] = {}

    def _market_open_time(self, symbol: str, now: datetime) -> Optional[datetime]:
        """根据标的推断开盘时间（本地时区）。"""
        if symbol.endswith('.HK'):
            return now.replace(hour=9, minute=30, second=0, microsecond=0)
        if symbol.endswith('.US'):
            # 本地（Asia/Shanghai）对应美股常规时段开盘21:30
            base = now.replace(hour=21, minute=30, second=0, microsecond=0)
            # 若当前在凌晨0-4点，则开盘属于“前一日的晚上”，回退一天
            if now.hour < 9:
                base = base - timedelta(days=1)
            return base
        # 其它市场暂不处理
        return None

    def _ensure_state(self, symbol: str, now: datetime) -> Optional[ORBState]:
        opn = self._market_open_time(symbol, now)
        if not opn:
            return None

        # 当日初始化或跨日重置
        cur_day = opn.date()
        st = self.state.get(symbol)
        if not st or st.day != cur_day:
            window_end = opn + timedelta(minutes=self.window_minutes)
            st = ORBState(day=cur_day, window_end=window_end, high=0.0, low=float('inf'), armed=False, triggered=False)
            self.state[symbol] = st
            logger.info(f"[{symbol}] ORB窗口 {opn.time()} - {window_end.time()} 已设定")
        return st

    async def run(self) -> None:
        logger.info("=" * 70)
        logger.info(f"🚀 启动 ORB 策略：窗口={self.window_minutes}分钟, 预算={self.budget_pct*100:.1f}%")
        logger.info("=" * 70)

        # 载入监控标的
        wl = WatchlistLoader().load()
        symbols = wl.symbols()
        if not symbols:
            logger.warning("监控列表为空，退出")
            return

        async with QuoteDataClient(self.settings) as quote_client:
            try:
                while True:
                    now = datetime.now(self.beijing_tz)

                    # 批量拉取行情，降低请求次数
                    quotes = await quote_client.get_realtime_quote(symbols)
                    if not quotes:
                        await asyncio.sleep(2)
                        continue

                    for q in quotes:
                        symbol = q.symbol
                        price = float(q.last_done) if q.last_done else 0.0
                        if price <= 0:
                            continue

                        st = self._ensure_state(symbol, now)
                        if not st:
                            continue

                        # 若未到当天开盘，跳过
                        opn = self._market_open_time(symbol, now)
                        if now < opn:
                            continue

                        # 窗口内：更新高低
                        if now <= st.window_end:
                            st.high = max(st.high, price)
                            st.low = min(st.low, price)
                            st.armed = False
                            continue

                        # 窗口结束：开始等待突破
                        if not st.armed and st.high > 0 and st.low < float('inf'):
                            st.armed = True
                            logger.info(f"[{symbol}] 开盘区间完成: 高={st.high:.3f} 低={st.low:.3f}")

                        # 仅做多：突破区间高点
                        if st.armed and not st.triggered and price >= st.high:
                            # 生成信号（含策略名与预算）
                            signal = {
                                'symbol': symbol,
                                'type': 'BUY',
                                'side': 'BUY',
                                'score': self.base_score,
                                'price': price,
                                'reasons': [f'ORB 向上突破({st.high:.3f})'],
                                'strategy': 'ORB',
                                'budget_pct': self.budget_pct,
                                'stop_loss': round(st.low * 0.99, 4),  # 简单保护：区间低点下方1%
                            }

                            ok = await self.signal_queue.publish_signal(signal)
                            if ok:
                                st.triggered = True
                                logger.success(f"[{symbol}] ✅ ORB 突破信号已发布，价格={price:.3f}")

                    await asyncio.sleep(2)

            finally:
                await self.signal_queue.close()


async def main():
    import argparse

    parser = argparse.ArgumentParser(description='ORB 策略信号生成器')
    # 同时支持 --account 与 --account-id 两种写法
    parser.add_argument('--account', dest='account', default=None, help='账号ID（可选，等同于 --account-id）')
    parser.add_argument('--account-id', dest='account_id', default=None, help='账号ID（可选）')
    parser.add_argument('--window', dest='window', type=int, default=15, help='开盘区间分钟数（默认15）')
    parser.add_argument('--budget-pct', dest='budget_pct', type=float, default=0.10, help='每次信号预算占比（默认0.10=10%）')
    parser.add_argument('--score', dest='score', type=int, default=60, help='信号评分（默认60）')
    args = parser.parse_args()

    account_id = args.account_id or args.account
    strategy = ORBStrategy(
        account_id=account_id,
        window_minutes=args.window,
        budget_pct=args.budget_pct,
        score=args.score,
    )
    await strategy.run()


if __name__ == '__main__':
    asyncio.run(main())
