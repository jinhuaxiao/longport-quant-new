"""Soft exit engine: event-driven exit signals (Chandelier/Donchian).

Publishes SELL signals to Redis queue when soft exit triggers are hit.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from loguru import logger
from longport import openapi

from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient
from longport_quant.features.technical_indicators import TechnicalIndicators
from longport_quant.messaging.signal_queue import SignalQueue


class SoftExitEngine:
    """Compute soft-exit triggers and publish SELL signals."""

    def __init__(self, account_id: str | None = None) -> None:
        self.settings = get_settings(account_id=account_id)
        self.account_id = account_id or "default"

        # Runtime state
        self._chandelier_stop: Dict[str, float] = {}
        self._last_published_at: Dict[str, float] = {}

        # Helpers
        self.signal_queue = SignalQueue(
            redis_url=self.settings.redis_url,
            queue_key=self.settings.signal_queue_key,
            processing_key=self.settings.signal_processing_key,
            failed_key=self.settings.signal_failed_key,
            max_retries=self.settings.signal_max_retries,
        )

        self._period = getattr(openapi.Period, self.settings.soft_exit_period, openapi.Period.Min_5)
        self._atr_n = int(self.settings.soft_exit_atr_period)
        self._ch_k = float(self.settings.soft_exit_chandelier_k)
        self._donchian_n = int(self.settings.soft_exit_donchian_n)
        self._poll = int(self.settings.soft_exit_poll_interval)
        self._cooldown = int(self.settings.soft_exit_signal_cooldown)

    async def run(self) -> None:
        """Main loop: poll quotes and publish exit signals when triggered."""
        logger.info("=" * 70)
        logger.info("🧠 启动 SoftExit 引擎（Chandelier/Donchian）")
        logger.info("=" * 70)

        async with QuoteDataClient(self.settings) as quote_client, \
                   LongportTradingClient(self.settings) as trade_client:
            self.quote_client = quote_client
            self.trade_client = trade_client

            while True:
                try:
                    account = await self.trade_client.get_account()
                    positions = account.get("positions", [])

                    if not positions:
                        logger.debug("📭 无持仓，等待...")
                        await asyncio.sleep(self._poll)
                        continue

                    # Process in small batches to avoid API throttling
                    symbols = [p["symbol"] for p in positions]
                    await self._process_positions(account, positions)

                except Exception as e:
                    logger.error(f"SoftExit循环错误: {e}")

                await asyncio.sleep(self._poll)

    async def _process_positions(self, account: Dict, positions: List[Dict]) -> None:
        now_ts = datetime.now(timezone.utc).timestamp()

        for pos in positions:
            try:
                symbol = pos["symbol"]
                qty = int(pos.get("available_quantity") or pos.get("quantity") or 0)
                if qty <= 0:
                    continue

                # Cooldown per symbol
                last_pub = self._last_published_at.get(symbol, 0)
                if now_ts - last_pub < self._cooldown:
                    continue

                # Fetch candles (enough for ATR/Donchian)
                count = max(self._atr_n, self._donchian_n) + 5
                candles = await self.quote_client.get_candlesticks(
                    symbol=symbol,
                    period=self._period,
                    count=count,
                    adjust_type=openapi.AdjustType.NoAdjust,
                )

                if not candles or len(candles) < max(self._atr_n, self._donchian_n) + 2:
                    logger.debug(f"{symbol} K线不足，跳过（需要>{max(self._atr_n, self._donchian_n)+2}）")
                    continue

                highs = [float(c.high) for c in candles]
                lows = [float(c.low) for c in candles]
                closes = [float(c.close) for c in candles]

                atr_series = TechnicalIndicators.atr(highs, lows, closes, period=self._atr_n)
                atr = float(atr_series[-1]) if atr_series[-1] == atr_series[-1] else 0.0

                # Chandelier Stop_t
                hh_n = max(highs[-self._atr_n :])
                new_stop = hh_n - self._ch_k * atr if atr > 0 else None
                if new_stop is not None:
                    prev = self._chandelier_stop.get(symbol, new_stop)
                    stop_t = max(prev, new_stop)
                    self._chandelier_stop[symbol] = stop_t
                else:
                    stop_t = None

                last = closes[-1]

                triggered = False
                reason = None
                score = 0
                exit_type = "SELL"

                if stop_t is not None and last <= stop_t:
                    triggered = True
                    reason = f"Chandelier Exit: last={last:.2f} <= stop={stop_t:.2f} (N={self._atr_n}, k={self._ch_k})"
                    score = 95

                # Donchian lower break (crossing)
                if not triggered:
                    ln = min(lows[-self._donchian_n :])
                    prev_close = closes[-2]
                    prev_ln = min(lows[-(self._donchian_n + 1) : -1])
                    if prev_close > prev_ln and last <= ln:
                        triggered = True
                        reason = f"Donchian Break: close↓N-low (N={self._donchian_n})"
                        score = 90

                if not triggered:
                    continue

                # Build SELL signal
                signal = {
                    "symbol": symbol,
                    "type": exit_type,
                    "side": "SELL",
                    "quantity": qty,
                    "price": last,
                    "reason": reason,
                    "score": score,
                    "timestamp": datetime.now().isoformat(),
                    # For notification enrichment
                    "cost_price": pos.get("cost_price"),
                    "entry_time": pos.get("entry_time"),
                    "indicators": {
                        "atr": atr,
                        "chandelier_stop": stop_t,
                        "donchian_low": min(lows[-self._donchian_n :]),
                        "hh_n": hh_n if atr > 0 else None,
                    },
                    "exit_score_details": [reason],
                }

                ok = await self.signal_queue.publish_signal(signal, priority=score)
                if ok:
                    self._last_published_at[symbol] = now_ts
                    logger.success(f"📤 发布软退出信号: {symbol} {reason}")

            except Exception as e:
                logger.error(f"处理 {pos.get('symbol','?')} 软退出失败: {e}")


__all__ = ["SoftExitEngine"]

