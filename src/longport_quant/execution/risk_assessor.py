"""Risk assessment module for intelligent backup order decision."""

from __future__ import annotations

from typing import Dict, Any
from loguru import logger


class RiskAssessor:
    """
    评估交易风险，决定是否需要提交备份条件单

    基于多维度风险因素计算风险分数：
    - ATR波动率（40%权重）
    - 价格水平（20%权重）
    - 信号强度（20%权重）
    - 止损幅度（20%权重）
    """

    def __init__(self, config: Any):
        """
        初始化风险评估器

        Args:
            config: BackupOrderConfig配置对象
        """
        self.config = config

    def assess(
        self,
        symbol: str,
        signal: Dict[str, Any],
        quantity: int,
        price: float
    ) -> Dict[str, Any]:
        """
        综合评估交易风险

        Args:
            symbol: 标的代码 (e.g., "1398.HK", "NVDA.US")
            signal: 信号字典（包含indicators, score, stop_loss等）
            quantity: 交易数量
            price: 交易价格

        Returns:
            {
                'should_backup': bool,  # 是否需要提交备份条件单
                'risk_score': int,      # 风险分数 (0-100)
                'factors': dict,        # 各因素得分详情
                'reason': str           # 决策原因
            }
        """
        risk_score = 0
        factors = {}

        # 1. ATR波动率评估 (40%权重)
        atr_score = self._assess_atr(symbol, signal, price)
        risk_score += atr_score
        factors['ATR波动率'] = atr_score

        # 2. 价格水平评估 (20%权重)
        price_score = self._assess_price_level(symbol, price)
        risk_score += price_score
        factors['价格水平'] = price_score

        # 3. 信号强度评估 (20%权重)
        signal_score = self._assess_signal_strength(signal)
        risk_score += signal_score
        factors['信号强度'] = signal_score

        # 4. 止损幅度评估 (20%权重)
        stop_loss_score = self._assess_stop_loss_width(signal, price)
        risk_score += stop_loss_score
        factors['止损幅度'] = stop_loss_score

        # 5. 持仓价值强制规则
        position_value = quantity * price
        high_value_threshold = 50000.0  # HKD or USD

        # 决策逻辑
        should_backup = risk_score >= self.config.risk_threshold
        reason = f"风险分数{risk_score} >= 阈值{self.config.risk_threshold}"

        # 高价值持仓强制启用
        if not should_backup and position_value > high_value_threshold:
            should_backup = True
            reason = f"高价值持仓(${position_value:,.0f}) 强制启用备份保护"
            factors['高价值持仓'] = f"${position_value:,.0f} > ${high_value_threshold:,.0f}"

        return {
            'should_backup': should_backup,
            'risk_score': risk_score,
            'factors': factors,
            'reason': reason,
            'position_value': position_value
        }

    def _assess_atr(self, symbol: str, signal: Dict, price: float) -> int:
        """
        评估ATR波动率风险

        ATR比率 = ATR / 价格
        - >3%: 高风险 → 40分
        - >2%: 中高风险 → 25分
        - >1.5%: 中风险 → 15分
        - ≤1.5%: 低风险 → 0分
        """
        indicators = signal.get('indicators', {})
        atr = indicators.get('atr', 0)

        if price <= 0 or atr <= 0:
            return 0

        atr_ratio = (atr / price)

        if atr_ratio >= self.config.atr_ratio_high:
            return self.config.atr_weight  # 40分
        elif atr_ratio >= self.config.atr_ratio_medium:
            return int(self.config.atr_weight * 0.625)  # 25分
        elif atr_ratio >= self.config.atr_ratio_low:
            return int(self.config.atr_weight * 0.375)  # 15分
        else:
            return 0

    def _assess_price_level(self, symbol: str, price: float) -> int:
        """
        评估价格水平风险

        港股:
        - >$100: 高价股，tick size大 → 20分
        - <$1: 低价股，波动大 → 15分

        美股:
        - >$500: 高价股 → 20分
        - <$5: 低价股 → 15分
        """
        if ".HK" in symbol:
            # 港股
            if price > 100:
                return self.config.price_weight  # 20分
            elif price < 1:
                return int(self.config.price_weight * 0.75)  # 15分
        elif ".US" in symbol:
            # 美股
            if price > 500:
                return self.config.price_weight  # 20分
            elif price < 5:
                return int(self.config.price_weight * 0.75)  # 15分

        return 0

    def _assess_signal_strength(self, signal: Dict) -> int:
        """
        评估信号强度风险

        - 评分<60: 信号较弱 → 20分
        - 评分≥60: 信号较强 → 0分
        """
        score = signal.get('score', 100)  # 默认假设强信号

        if score < self.config.weak_signal_threshold:
            return self.config.signal_weight  # 20分

        return 0

    def _assess_stop_loss_width(self, signal: Dict, price: float) -> int:
        """
        评估止损幅度风险

        止损幅度 = |entry_price - stop_loss| / entry_price
        - >5%: 波动空间大 → 20分
        - ≤5%: 波动空间小 → 0分
        """
        stop_loss = signal.get('stop_loss', 0)

        if price <= 0 or stop_loss <= 0:
            return 0

        stop_loss_pct = abs(price - stop_loss) / price

        if stop_loss_pct > self.config.wide_stop_loss_pct:
            return self.config.stop_loss_weight  # 20分

        return 0

    def format_assessment_log(self, assessment: Dict[str, Any]) -> str:
        """
        格式化评估结果为日志字符串

        Args:
            assessment: assess()方法返回的评估结果

        Returns:
            格式化的日志字符串
        """
        lines = []
        lines.append(
            f"📊 风险评估: 总分={assessment['risk_score']}/100 "
            f"(阈值={self.config.risk_threshold})"
        )

        for factor, score in assessment['factors'].items():
            if isinstance(score, int):
                lines.append(f"  - {factor}: {score}分")
            else:
                lines.append(f"  - {factor}: {score}")

        if assessment['should_backup']:
            lines.append(f"🛡️ {assessment['reason']}")
        else:
            lines.append(f"ℹ️ {assessment['reason']}")

        return "\n".join(lines)


__all__ = ["RiskAssessor"]
