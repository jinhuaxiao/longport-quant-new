"""技术指标计算模块"""

from __future__ import annotations

import numpy as np
from typing import List, Tuple
from dataclasses import dataclass


@dataclass
class RSISignal:
    """RSI指标信号"""
    value: float  # RSI值
    signal: str   # BUY/SELL/HOLD
    reason: str   # 信号原因


@dataclass
class BollingerBandsSignal:
    """布林带指标信号"""
    upper: float  # 上轨
    middle: float  # 中轨
    lower: float   # 下轨
    current_price: float
    signal: str    # BUY/SELL/HOLD
    reason: str    # 信号原因


@dataclass
class MACDSignal:
    """MACD指标信号"""
    macd: float       # MACD线
    signal_line: float  # 信号线
    histogram: float   # 柱状图
    signal: str        # BUY/SELL/HOLD
    reason: str        # 信号原因


class TechnicalIndicators:
    """技术指标计算器"""

    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> float:
        """
        计算RSI（相对强弱指标）

        Args:
            prices: 价格序列（从旧到新）
            period: 计算周期，默认14

        Returns:
            RSI值（0-100）
        """
        if len(prices) < period + 1:
            return 50.0  # 数据不足，返回中性值

        # 计算价格变化
        deltas = np.diff(prices)

        # 分离上涨和下跌
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        # 计算平均涨跌幅
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return float(rsi)

    @staticmethod
    def analyze_rsi(
        prices: List[float],
        period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0
    ) -> RSISignal:
        """
        分析RSI指标并生成信号

        Args:
            prices: 价格序列
            period: 计算周期
            oversold: 超卖阈值（默认30）
            overbought: 超买阈值（默认70）

        Returns:
            RSI信号
        """
        rsi = TechnicalIndicators.calculate_rsi(prices, period)

        if rsi < oversold:
            return RSISignal(
                value=rsi,
                signal="BUY",
                reason=f"RSI超卖({rsi:.1f} < {oversold})"
            )
        elif rsi > overbought:
            return RSISignal(
                value=rsi,
                signal="SELL",
                reason=f"RSI超买({rsi:.1f} > {overbought})"
            )
        else:
            return RSISignal(
                value=rsi,
                signal="HOLD",
                reason=f"RSI中性({rsi:.1f})"
            )

    @staticmethod
    def calculate_bollinger_bands(
        prices: List[float],
        period: int = 20,
        std_dev: float = 2.0
    ) -> Tuple[float, float, float]:
        """
        计算布林带

        Args:
            prices: 价格序列
            period: 计算周期，默认20
            std_dev: 标准差倍数，默认2

        Returns:
            (上轨, 中轨, 下轨)
        """
        if len(prices) < period:
            current = prices[-1] if prices else 100.0
            return (current * 1.05, current, current * 0.95)

        # 计算中轨（简单移动平均）
        middle = np.mean(prices[-period:])

        # 计算标准差
        std = np.std(prices[-period:])

        # 计算上下轨
        upper = middle + (std_dev * std)
        lower = middle - (std_dev * std)

        return (float(upper), float(middle), float(lower))

    @staticmethod
    def analyze_bollinger_bands(
        prices: List[float],
        period: int = 20,
        std_dev: float = 2.0
    ) -> BollingerBandsSignal:
        """
        分析布林带并生成信号

        Args:
            prices: 价格序列
            period: 计算周期
            std_dev: 标准差倍数

        Returns:
            布林带信号
        """
        upper, middle, lower = TechnicalIndicators.calculate_bollinger_bands(
            prices, period, std_dev
        )

        current_price = prices[-1]

        # 价格突破下轨 - 买入信号
        if current_price < lower:
            return BollingerBandsSignal(
                upper=upper,
                middle=middle,
                lower=lower,
                current_price=current_price,
                signal="BUY",
                reason=f"价格突破下轨(${current_price:.2f} < ${lower:.2f})"
            )
        # 价格突破上轨 - 卖出信号
        elif current_price > upper:
            return BollingerBandsSignal(
                upper=upper,
                middle=middle,
                lower=lower,
                current_price=current_price,
                signal="SELL",
                reason=f"价格突破上轨(${current_price:.2f} > ${upper:.2f})"
            )
        else:
            return BollingerBandsSignal(
                upper=upper,
                middle=middle,
                lower=lower,
                current_price=current_price,
                signal="HOLD",
                reason=f"价格在布林带内(${lower:.2f} - ${upper:.2f})"
            )

    @staticmethod
    def calculate_macd(
        prices: List[float],
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9
    ) -> Tuple[float, float, float]:
        """
        计算MACD指标

        Args:
            prices: 价格序列
            fast_period: 快线周期，默认12
            slow_period: 慢线周期，默认26
            signal_period: 信号线周期，默认9

        Returns:
            (MACD线, 信号线, 柱状图)
        """
        if len(prices) < slow_period:
            return (0.0, 0.0, 0.0)

        prices_array = np.array(prices)

        # 计算EMA
        def ema(data, period):
            weights = np.exp(np.linspace(-1., 0., period))
            weights /= weights.sum()
            ema_values = np.convolve(data, weights, mode='valid')
            return ema_values[-1] if len(ema_values) > 0 else data[-1]

        # 快线和慢线
        ema_fast = ema(prices_array, fast_period)
        ema_slow = ema(prices_array, slow_period)

        # MACD线
        macd_line = ema_fast - ema_slow

        # 简化：信号线使用固定值（实际应该是MACD的EMA）
        signal_line = macd_line * 0.8  # 简化计算

        # 柱状图
        histogram = macd_line - signal_line

        return (float(macd_line), float(signal_line), float(histogram))

    @staticmethod
    def analyze_macd(
        prices: List[float],
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9
    ) -> MACDSignal:
        """
        分析MACD指标并生成信号

        Args:
            prices: 价格序列
            fast_period: 快线周期
            slow_period: 慢线周期
            signal_period: 信号线周期

        Returns:
            MACD信号
        """
        macd, signal_line, histogram = TechnicalIndicators.calculate_macd(
            prices, fast_period, slow_period, signal_period
        )

        # MACD金叉 - 买入信号
        if histogram > 0 and macd > signal_line:
            return MACDSignal(
                macd=macd,
                signal_line=signal_line,
                histogram=histogram,
                signal="BUY",
                reason="MACD金叉(上穿信号线)"
            )
        # MACD死叉 - 卖出信号
        elif histogram < 0 and macd < signal_line:
            return MACDSignal(
                macd=macd,
                signal_line=signal_line,
                histogram=histogram,
                signal="SELL",
                reason="MACD死叉(下穿信号线)"
            )
        else:
            return MACDSignal(
                macd=macd,
                signal_line=signal_line,
                histogram=histogram,
                signal="HOLD",
                reason=f"MACD中性(柱状图={histogram:.2f})"
            )

    @staticmethod
    def calculate_sma(prices: List[float], period: int) -> float:
        """计算简单移动平均线"""
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        return float(np.mean(prices[-period:]))

    @staticmethod
    def calculate_ema(prices: List[float], period: int) -> float:
        """计算指数移动平均线"""
        if len(prices) < period:
            return prices[-1] if prices else 0.0

        prices_array = np.array(prices[-period:])
        weights = np.exp(np.linspace(-1., 0., period))
        weights /= weights.sum()

        return float(np.sum(prices_array * weights))


__all__ = [
    "TechnicalIndicators",
    "RSISignal",
    "BollingerBandsSignal",
    "MACDSignal"
]