"""Regime-based de-risking rebalancer: 从满仓回落到目标仓位/购买力。

计算需要减仓的总额，并按等比例在现有持仓中生成 SELL 信号（按手数取整）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from loguru import logger

from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient
from longport_quant.messaging.signal_queue import SignalQueue
from longport_quant.risk.regime import RegimeClassifier
from longport_quant.utils import LotSizeHelper
from longport_quant.features.technical_indicators import TechnicalIndicators


@dataclass
class RebalancePlanItem:
    symbol: str
    currency: str
    price: float
    sell_qty: int
    reason: str


class RegimeRebalancer:
    def __init__(self, account_id: str | None = None) -> None:
        self.settings = get_settings(account_id=account_id)
        self.account_id = account_id or "default"
        self.signal_queue = SignalQueue(
            redis_url=self.settings.redis_url,
            queue_key=self.settings.signal_queue_key,
            processing_key=self.settings.signal_processing_key,
            failed_key=self.settings.signal_failed_key,
            max_retries=self.settings.signal_max_retries,
        )
        self.regime = RegimeClassifier(self.settings)
        self.lot_helper = LotSizeHelper()

    async def run_once(self) -> Tuple[str, List[RebalancePlanItem]]:
        """执行一次去杠杆计划：生成并发布 SELL 信号。

        Returns:
            (regime_label, plan_items)
        """
        async with QuoteDataClient(self.settings) as quote, LongportTradingClient(self.settings) as trade:
            # 1) 判别 Regime 与日内风格 → 计算最终 reserve
            res = await self.regime.classify(quote)
            regime = res.regime

            reserve_map = {
                "BULL": float(getattr(self.settings, 'regime_reserve_pct_bull', 0.15) or 0.15),
                "RANGE": float(getattr(self.settings, 'regime_reserve_pct_range', 0.30) or 0.30),
                "BEAR": float(getattr(self.settings, 'regime_reserve_pct_bear', 0.50) or 0.50),
            }
            reserve = reserve_map.get(regime, 0.30)

            # 日内风格微调（可选）
            if getattr(self.settings, 'intraday_style_enabled', False):
                try:
                    style, _ = await self.regime.classify_intraday_style(quote)
                    delta = (
                        float(getattr(self.settings, 'intraday_reserve_delta_trend', -0.05)) if style == 'TREND'
                        else float(getattr(self.settings, 'intraday_reserve_delta_range', 0.05))
                    )
                    reserve = min(max(reserve + delta, 0.0), 0.9)
                except Exception as e:
                    logger.debug(f"日内风格微调失败（忽略）: {e}")

            # 2) 拉取账户与持仓
            account = await trade.get_account()
            positions: List[Dict] = account.get("positions", [])
            if not positions:
                logger.info("无持仓，无需去杠杆")
                return regime, []

            # 3) 拉取价格
            symbols = [p["symbol"] for p in positions]
            quotes = await quote.get_realtime_quote(symbols)
            price_map: Dict[str, float] = {}
            for q in quotes:
                try:
                    price_map[q.symbol] = float(q.last_done)
                except Exception:
                    continue

            # 4) 按币种分别计算：当前持仓市值、目标持仓市值 → 需要减仓金额
            #    采用等比例削减方案，确保快速回落到目标仓位
            by_currency: Dict[str, List[Dict]] = {}
            for p in positions:
                ccy = p.get("currency") or ("HKD" if p.get("symbol", "").endswith('.HK') else 'USD')
                by_currency.setdefault(ccy, []).append(p)

            plan: List[RebalancePlanItem] = []

            for ccy, items in by_currency.items():
                equity = float(account.get("net_assets", {}).get(ccy, 0) or 0)
                if equity <= 0:
                    continue

                # 计算当前持仓总市值
                total_value = 0.0
                values: Dict[str, float] = {}
                for p in items:
                    sym = p["symbol"]
                    price = price_map.get(sym, 0.0)
                    qty = int(p.get("available_quantity") or p.get("quantity") or 0)
                    if price > 0 and qty > 0:
                        v = price * qty
                        values[sym] = v
                        total_value += v

                if total_value <= 0:
                    continue

                # 目标持仓市值（预留现金 reserve）
                target_value = equity * (1.0 - reserve)
                if total_value <= target_value:
                    logger.info(f"{ccy}: 当前持仓${total_value:,.0f} ≤ 目标${target_value:,.0f}，无需减仓")
                    continue

                cut_value = total_value - target_value
                logger.info(f"{ccy}: 减仓目标 ${cut_value:,.0f} （当前${total_value:,.0f} → 目标${target_value:,.0f}，预留{reserve*100:.0f}%现金）")

                # 5) 弱势/形态破位优先：按“弱势评分”降序贪心削减
                #    评分要素：Donchian破位、跌破MA20/MA50、MACD死叉、SMA20下行
                metrics_cache: Dict[str, Tuple[int, str]] = {}

                async def weakness(sym: str) -> Tuple[int, str]:
                    if sym in metrics_cache:
                        return metrics_cache[sym]
                    # 获取日线K线用于指标
                    candles = await quote.get_candlesticks(
                        symbol=sym,
                        period=openapi.Period.Day,
                        count=60,
                        adjust_type=openapi.AdjustType.NoAdjust,
                    )
                    score = 0
                    reasons = []
                    try:
                        if candles and len(candles) >= 30:
                            closes = [float(c.close) for c in candles]
                            highs = [float(c.high) for c in candles]
                            lows = [float(c.low) for c in candles]
                            last = closes[-1]

                            # 均线
                            sma20 = TechnicalIndicators.sma(closes, 20)[-1]
                            sma50 = TechnicalIndicators.sma(closes, 50)[-1] if len(closes) >= 50 else None
                            if not (sma20 != sma20):  # 非NaN
                                if last < sma20:
                                    score += 15
                                    reasons.append("跌破MA20")
                            if sma50 is not None and not (sma50 != sma50):
                                if last < sma50:
                                    score += 25
                                    reasons.append("跌破MA50")

                            # Donchian下轨破位（20日）
                            if len(lows) >= 20:
                                dn = min(lows[-20:])
                                if last <= dn:
                                    score += 40
                                    reasons.append("跌破Donchian下轨(20)")

                            # MACD死叉/空头
                            macd = TechnicalIndicators.macd(closes, 12, 26, 9)
                            hist = macd['histogram']
                            if len(hist) >= 2 and not (hist[-1] != hist[-1]) and not (hist[-2] != hist[-2]):
                                if hist[-1] < 0 and hist[-2] > 0:
                                    score += 15
                                    reasons.append("MACD死叉")
                                elif hist[-1] < 0:
                                    score += 5
                                    reasons.append("MACD空头")

                            # SMA20斜率为负
                            sma20_series = TechnicalIndicators.sma(closes, 20)
                            if len(sma20_series) >= 2 and not (sma20_series[-1] != sma20_series[-1]) and not (sma20_series[-2] != sma20_series[-2]):
                                if sma20_series[-1] < sma20_series[-2]:
                                    score += 5
                                    reasons.append("MA20下行")

                        else:
                            reasons.append("数据不足")
                    except Exception as e:
                        reasons.append(f"指标失败:{e}")

                    text = ",".join(reasons) if reasons else "弱势不明显"
                    metrics_cache[sym] = (score, text)
                    return metrics_cache[sym]

                # 准备可削减列表
                sortable: List[Tuple[str, int, str, float, int]] = []  # (symbol, score, reasons, price, qty_avail)
                for p in items:
                    sym = p["symbol"]
                    price = price_map.get(sym, 0.0)
                    qty_avail = int(p.get("available_quantity") or p.get("quantity") or 0)
                    v = values.get(sym, 0.0)
                    if price <= 0 or qty_avail <= 0 or v <= 0:
                        continue
                    sc, rs = await weakness(sym)
                    sortable.append((sym, sc, rs, price, qty_avail))

                sortable.sort(key=lambda x: x[1], reverse=True)

                remaining = cut_value
                for sym, sc, rs, price, qty_avail in sortable:
                    if remaining <= 0:
                        break
                    # 单票最多削到可用数量（整手）
                    lot = await self.lot_helper.get_lot_size(sym, quote)
                    max_qty = qty_avail - (qty_avail % lot)
                    if max_qty <= 0:
                        continue
                    # 优先把弱势票清到满足剩余金额
                    target_qty = int(remaining / price)
                    raw_qty = min(max_qty, target_qty)
                    sell_qty = (raw_qty // lot) * lot
                    if sell_qty <= 0 and target_qty > 0:
                        # 至少卖一手
                        sell_qty = min(max_qty, lot)
                    if sell_qty <= 0:
                        continue
                    reason = f"Regime去杠杆(弱势优先): {regime} 预留{reserve*100:.0f}%现金 | {rs} (分{sc}分)"
                    plan.append(RebalancePlanItem(sym, ccy, price, sell_qty, reason))
                    remaining -= sell_qty * price

            # 6) 发布 SELL 信号（由 OrderExecutor 执行）
            for item in plan:
                signal = {
                    'symbol': item.symbol,
                    'type': 'SELL',
                    'side': 'SELL',
                    'quantity': item.sell_qty,
                    'price': item.price,
                    'reason': item.reason,
                    'score': 85,  # 高优先级处理
                    'timestamp': None,
                    'priority': 85,
                }
                ok = await self.signal_queue.publish_signal(signal, priority=signal['priority'])
                if ok:
                    logger.success(f"📤 发布减仓信号: {item.symbol} 卖{item.sell_qty}股 @~${item.price:.2f} | {item.reason}")

            return regime, plan


__all__ = ["RegimeRebalancer", "RebalancePlanItem"]
