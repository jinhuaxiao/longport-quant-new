"""Regime classifier: 牛/熊/震荡，基于指数均线（简化版）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from loguru import logger
from longport import openapi

from longport_quant.config.settings import Settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.utils.market_hours import MarketHours


@dataclass
class RegimeResult:
    regime: str  # 'BULL' | 'BEAR' | 'RANGE'
    details: str
    active_market: str = ""  # 'HK' | 'US' | 'NONE'


class RegimeClassifier:
    """简单规则：指数收盘价相对MA的占比决定牛/熊/震荡。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._period = openapi.Period.Day
        self._ma_n = int(settings.regime_ma_period)

    def _parse_symbols(self, filter_by_market: bool = True) -> List[str]:
        """
        解析指数符号列表

        Args:
            filter_by_market: 是否根据当前市场时段过滤指数

        Returns:
            指数符号列表
        """
        raw = (self._settings.regime_index_symbols or "").strip()
        if not raw:
            return []

        if not filter_by_market:
            # 不过滤，返回所有配置的指数
            return [s.strip() for s in raw.split(',') if s.strip()]

        # 根据当前市场时段过滤指数
        active_symbols = MarketHours.get_active_index_symbols(raw)
        if not active_symbols:
            return []

        return [s.strip() for s in active_symbols.split(',') if s.strip()]

    def _parse_inverse_symbols(self, filter_by_market: bool = True) -> List[str]:
        """
        解析反向指标列表（如VIX）

        Args:
            filter_by_market: 是否根据当前市场时段过滤指数

        Returns:
            反向指标符号列表
        """
        raw = (self._settings.regime_inverse_symbols or "").strip()
        if not raw:
            return []

        if not filter_by_market:
            # 不过滤，返回所有配置的反向指标
            return [s.strip() for s in raw.split(',') if s.strip()]

        # 根据当前市场时段过滤反向指标
        # VIX 等美股指标通常以 ^ 开头或 .US 结尾
        current_market = MarketHours.get_current_market()

        # 如果不在交易时段，返回空
        if current_market == "NONE":
            return []

        symbols = [s.strip() for s in raw.split(',') if s.strip()]
        filtered = []

        for sym in symbols:
            # 美股时段：包含 .US 结尾或 ^ 开头的符号
            if current_market == "US" and (sym.endswith('.US') or sym.startswith('^')):
                filtered.append(sym)
            # 港股时段：包含 .HK 结尾的符号
            elif current_market == "HK" and sym.endswith('.HK'):
                filtered.append(sym)

        return filtered

    async def classify(self, quote: QuoteDataClient, filter_by_market: bool = True) -> RegimeResult:
        """
        分类市场状态

        Args:
            quote: 行情客户端
            filter_by_market: 是否根据当前市场时段过滤指数（默认True）

        Returns:
            RegimeResult 包含市场状态和活跃市场信息
        """
        # 获取当前活跃市场
        current_market = MarketHours.get_current_market()

        # 获取正向和反向指标
        symbols = self._parse_symbols(filter_by_market=filter_by_market)
        inverse_symbols = self._parse_inverse_symbols(filter_by_market=filter_by_market)

        if not symbols and not inverse_symbols:
            if filter_by_market and current_market == "NONE":
                return RegimeResult("RANGE", "非交易时段", active_market="NONE")
            return RegimeResult("RANGE", "无指数配置", active_market=current_market)

        ups = 0
        total = 0
        used_normal = []
        used_inverse = []

        # 处理正向指标（普通指数）
        for sym in symbols:
            try:
                candles = await quote.get_candlesticks(
                    symbol=sym,
                    period=self._period,
                    count=max(self._ma_n + 5, 210),
                    adjust_type=openapi.AdjustType.NoAdjust,
                )
                if not candles or len(candles) < self._ma_n + 1:
                    continue
                closes = [float(c.close) for c in candles]
                last = closes[-1]
                ma = sum(closes[-self._ma_n:]) / self._ma_n
                total += 1
                used_normal.append(sym)
                if last >= ma:
                    ups += 1
                logger.debug(f"正向指标 {sym}: last={last:.2f}, MA{self._ma_n}={ma:.2f}, 看涨={last >= ma}")
            except Exception as e:
                logger.debug(f"获取{sym}数据失败: {e}")
                continue

        # 处理反向指标（如VIX：低于MA=市场平静=看涨）
        for sym in inverse_symbols:
            try:
                candles = await quote.get_candlesticks(
                    symbol=sym,
                    period=self._period,
                    count=max(self._ma_n + 5, 210),
                    adjust_type=openapi.AdjustType.NoAdjust,
                )
                if not candles or len(candles) < self._ma_n + 1:
                    continue
                closes = [float(c.close) for c in candles]
                last = closes[-1]
                ma = sum(closes[-self._ma_n:]) / self._ma_n
                total += 1
                used_inverse.append(sym)
                # 反向逻辑：低于MA表示看涨（市场平静）
                if last < ma:
                    ups += 1
                logger.debug(f"反向指标 {sym}: last={last:.2f}, MA{self._ma_n}={ma:.2f}, 看涨={last < ma}")
            except Exception as e:
                logger.debug(f"获取{sym}数据失败: {e}")
                continue

        if total == 0:
            return RegimeResult("RANGE", "指数数据不足", active_market=current_market)

        pct = ups / total
        if pct >= 0.6:
            regime = "BULL"
        elif pct <= 0.4:
            regime = "BEAR"
        else:
            regime = "RANGE"

        # 构建详细说明
        details_parts = []
        if used_normal:
            details_parts.append(f"{', '.join(used_normal)} 收盘在MA{self._ma_n}之上")
        if used_inverse:
            details_parts.append(f"{', '.join(used_inverse)} 低于MA{self._ma_n}（市场平静）")

        details = f"{ups}/{total} 指数看涨 ({'; '.join(details_parts)})"

        # 添加市场信息到详情
        market_name = MarketHours.get_market_name(current_market)
        if filter_by_market and current_market != "NONE":
            details = f"[{market_name}市场] {details}"

        return RegimeResult(regime, details, active_market=current_market)

    async def classify_intraday_style(self, quote: QuoteDataClient) -> Tuple[str, str]:
        """
        日内风格判别（简化版）

        逻辑：对配置指数集合，计算当日开盘前N分钟的开盘区间（OR），以及当日到当前的日内范围（DR）。
        若 DR/OR >= 阈值 且 最新价突破OR上下沿（留有buffer），视为“趋势日”；否则“震荡日”。

        Returns:
            (style, details) where style in { 'TREND', 'RANGE' }
        """
        settings = self._settings
        open_minutes = max(10, int(getattr(settings, 'intraday_open_minutes', 30)))
        expand_th = float(getattr(settings, 'intraday_trend_expand_threshold', 2.0) or 2.0)
        breakout_buf = float(getattr(settings, 'intraday_breakout_buffer_pct', 0.002) or 0.002)

        symbols = self._parse_symbols()
        if not symbols:
            return "RANGE", "无指数配置"

        votes_trend = 0
        used = []

        for sym in symbols:
            try:
                intraday = await quote.get_intraday(sym)
                # 兼容结构：假设 intraday.lines 或 data 点具有 high/low/price
                points = []
                if hasattr(intraday, 'lines') and intraday.lines:
                    points = intraday.lines
                elif hasattr(intraday, 'points') and intraday.points:
                    points = intraday.points
                else:
                    # 退化：用1分钟K线
                    candles = await quote.get_candlesticks(sym, openapi.Period.Min_1, 120, openapi.AdjustType.NoAdjust)
                    if not candles:
                        continue
                    points = candles

                # 提取当日序列的近似高低与开盘前N分钟区间
                highs = []
                lows = []
                closes = []
                for p in points:
                    h = getattr(p, 'high', None)
                    l = getattr(p, 'low', None)
                    c = getattr(p, 'close', None) or getattr(p, 'price', None)
                    if h is None or l is None or c is None:
                        continue
                    try:
                        highs.append(float(h))
                        lows.append(float(l))
                        closes.append(float(c))
                    except Exception:
                        continue

                if len(highs) < open_minutes + 5:
                    continue

                used.append(sym)
                or_high = max(highs[:open_minutes])
                or_low = min(lows[:open_minutes])
                dr_high = max(highs)
                dr_low = min(lows)
                last = closes[-1]

                or_w = max(1e-6, or_high - or_low)
                dr_w = max(1e-6, dr_high - dr_low)
                expand_ratio = dr_w / or_w

                breakout_up = last >= or_high * (1 + breakout_buf)
                breakout_dn = last <= or_low * (1 - breakout_buf)

                if expand_ratio >= expand_th and (breakout_up or breakout_dn):
                    votes_trend += 1
            except Exception as e:
                logger.debug(f"日内风格计算失败 {sym}: {e}")
                continue

        if not used:
            return "RANGE", "指数日内数据不足"

        style = "TREND" if votes_trend / len(used) >= 0.5 else "RANGE"
        return style, f"{votes_trend}/{len(used)} 指数满足 趋势扩张(≥{expand_th}×OR) 且突破OR"


__all__ = ["RegimeClassifier", "RegimeResult"]
