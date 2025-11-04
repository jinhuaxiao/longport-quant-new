"""Regime-based de-risking rebalancer: 从满仓回落到目标仓位/购买力。

计算需要减仓的总额，并按等比例在现有持仓中生成 SELL 信号（按手数取整）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from loguru import logger
from longport import openapi

from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient
from longport_quant.messaging.signal_queue import SignalQueue
from longport_quant.risk.regime import RegimeClassifier
from longport_quant.utils import LotSizeHelper
from longport_quant.utils.market_hours import MarketHours
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

                # 买入力检测：如果买入力为负，提高预留比例主动减仓
                buy_power_val = float(account.get("buy_power", {}).get(ccy, 0) or 0)
                original_reserve = reserve

                if buy_power_val < 0:
                    # 提高预留比例20%，最高不超过80%
                    reserve = min(reserve + 0.20, 0.80)
                    logger.warning(
                        f"⚠️ {ccy}买入力为负(${buy_power_val:,.0f})，提高预留比例主动减仓\n"
                        f"   预留比例: {original_reserve*100:.0f}% → {reserve*100:.0f}%\n"
                        f"   目的: 释放购买力，为新信号腾出资金"
                    )

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

            # 6) 检查市场时段 - 按symbol过滤（仅在配置启用时）
            if plan and self.settings.rebalancer_market_hours_only:
                # 显示当前时区信息（用于监控冬令时/夏令时转换）
                from datetime import datetime
                now_ny = datetime.now(MarketHours.US_TZ)
                now_hk = datetime.now(MarketHours.HK_TZ)

                logger.debug(
                    f"🕐 市场时区: "
                    f"NY={now_ny.strftime('%H:%M %Z(UTC%z)')} | "
                    f"HK={now_hk.strftime('%H:%M %Z(UTC%z)')}"
                )

                # 获取美股时段
                us_session = MarketHours.get_us_session()

                # 🌙 盘后时段特殊处理（16:00-20:00 ET）
                if us_session == "AFTERHOURS":
                    if not self.settings.enable_afterhours_rebalance:
                        logger.info(
                            f"⏸️ 美股盘后时段，ENABLE_AFTERHOURS_REBALANCE未启用，不执行减仓\n"
                            f"   当前时间: {now_ny.strftime('%H:%M %Z')}\n"
                            f"   说明: 盘后减仓功能默认禁用，需在配置中手动开启"
                        )
                        return regime, []

                    # 盘后时段：仅保留美股(.US)减仓信号
                    afterhours_plan = [item for item in plan if item.symbol.endswith(".US")]
                    filtered_count = len(plan) - len(afterhours_plan)

                    if filtered_count > 0:
                        logger.info(f"⏸️ 盘后时段，已过滤 {filtered_count} 个非美股标的")

                    if not afterhours_plan:
                        logger.warning(
                            f"⏸️ 盘后时段，计划中无美股标的，不执行减仓\n"
                            f"   当前时间: {now_ny.strftime('%H:%M %Z')}\n"
                            f"   计划标的: {', '.join([p.symbol for p in plan])}"
                        )
                        return regime, []

                    # 应用盘后仓位限制（单次最多减20%）
                    max_pct = self.settings.afterhours_max_position_pct
                    total_value_all = sum(p.sell_qty * p.price for p in afterhours_plan)
                    # 简化：这里直接用计划总金额，实际应该与总持仓比较
                    # 后续可以增强为：total_value_all / total_position_value <= max_pct

                    logger.warning(
                        f"🌙 盘后紧急减仓启动\n"
                        f"   时间: {now_ny.strftime('%H:%M %Z')}\n"
                        f"   Regime: {regime}\n"
                        f"   减仓标的: {len(afterhours_plan)}个美股\n"
                        f"   估算金额: ${total_value_all:,.0f}\n"
                        f"   风控: 强制限价单，紧急度≤{self.settings.afterhours_max_urgency}"
                    )

                    plan = afterhours_plan

                # ☀️ 常规交易时段（09:30-16:00 ET）
                elif us_session == "REGULAR":
                    # 过滤掉所属市场未开盘的symbol
                    valid_plan = []
                    filtered_symbols = []

                    for item in plan:
                        if MarketHours.is_market_open_for_symbol(item.symbol):
                            valid_plan.append(item)
                        else:
                            market = MarketHours.get_market_for_symbol(item.symbol)
                            filtered_symbols.append(f"{item.symbol}({market})")

                    # 记录过滤情况
                    if filtered_symbols:
                        logger.info(
                            f"⏸️ 已过滤 {len(filtered_symbols)} 个symbol（市场未开盘）: "
                            f"{', '.join(filtered_symbols[:5])}"
                            + (f" 等{len(filtered_symbols)}个" if len(filtered_symbols) > 5 else "")
                        )

                    # 如果所有symbol都被过滤，返回空计划
                    if not valid_plan:
                        total_qty = sum(p.sell_qty for p in plan)
                        total_value = sum(p.sell_qty * p.price for p in plan)
                        logger.warning(
                            f"⏸️ 所有减仓symbol所属市场都未开盘，暂不发布去杠杆信号\n"
                            f"   Regime状态: {regime}\n"
                            f"   计划卖单: {len(plan)}个标的\n"
                            f"   总数量: {total_qty}股\n"
                            f"   估算金额: ${total_value:,.0f}\n"
                            f"   将在下次检查周期重新评估"
                        )
                        return regime, []  # 返回空计划，不发布信号

                    plan = valid_plan

                # 🌃 市场关闭时段
                else:
                    logger.info(
                        f"⏸️ 市场关闭时段（{us_session}），不执行减仓\n"
                        f"   当前时间: {now_ny.strftime('%H:%M %Z')}"
                    )
                    return regime, []

                # 7) 币种与市场时段匹配检查（避免用错误指数评估）
                current_market = MarketHours.get_current_market()
                currency_filtered = []
                currency_skipped = []

                for item in plan:
                    # 港股时段：仅保留HKD币种（避免用HSI评估美股）
                    if current_market == "HK" and item.currency == "USD":
                        currency_skipped.append(f"{item.symbol}(USD)")
                        continue
                    # 美股时段：仅保留USD币种（避免用QQQ评估港股）
                    elif current_market == "US" and item.currency == "HKD":
                        currency_skipped.append(f"{item.symbol}(HKD)")
                        continue

                    currency_filtered.append(item)

                if currency_skipped:
                    logger.info(
                        f"⏸️ 已过滤 {len(currency_skipped)} 个标的（币种与当前市场不匹配）: "
                        f"{', '.join(currency_skipped[:5])}"
                        + (f" 等{len(currency_skipped)}个" if len(currency_skipped) > 5 else "")
                    )

                if not currency_filtered:
                    logger.warning(
                        f"⏸️ 所有减仓标的币种与当前市场不匹配，暂不发布信号\n"
                        f"   当前市场: {MarketHours.get_market_name(current_market)}\n"
                        f"   说明: {current_market}时段不评估其他币种持仓"
                    )
                    return regime, []

                plan = currency_filtered
                logger.info(f"✅ 市场+币种检查通过，将发布 {len(plan)} 个减仓信号")

            # 8) 发布 SELL 信号（由 OrderExecutor 执行）
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
