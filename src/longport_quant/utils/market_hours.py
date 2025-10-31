"""市场交易时间判断工具"""

from datetime import datetime, time
from zoneinfo import ZoneInfo
from typing import Literal

MarketType = Literal["HK", "US", "NONE"]


class MarketHours:
    """判断当前是哪个市场的交易时段"""

    # 港股交易时间 (香港时间 UTC+8)
    HK_MORNING_OPEN = time(9, 30)
    HK_MORNING_CLOSE = time(12, 0)
    HK_AFTERNOON_OPEN = time(13, 0)
    HK_AFTERNOON_CLOSE = time(16, 0)
    HK_TZ = ZoneInfo("Asia/Hong_Kong")

    # 美股交易时间 (美东时间 UTC-5/-4)
    US_OPEN = time(9, 30)
    US_CLOSE = time(16, 0)
    US_TZ = ZoneInfo("America/New_York")

    @classmethod
    def get_current_market(cls) -> MarketType:
        """
        获取当前是哪个市场的交易时段

        Returns:
            "HK": 港股交易时段
            "US": 美股交易时段
            "NONE": 都不在交易时段
        """
        now_hk = datetime.now(cls.HK_TZ)
        now_us = datetime.now(cls.US_TZ)

        # 检查是否是港股交易时间
        if cls._is_hk_trading_hours(now_hk):
            return "HK"

        # 检查是否是美股交易时间
        if cls._is_us_trading_hours(now_us):
            return "US"

        return "NONE"

    @classmethod
    def _is_hk_trading_hours(cls, dt: datetime) -> bool:
        """检查是否在港股交易时间"""
        # 排除周末
        if dt.weekday() >= 5:  # 5=周六, 6=周日
            return False

        current_time = dt.time()

        # 上午时段: 09:30-12:00
        if cls.HK_MORNING_OPEN <= current_time <= cls.HK_MORNING_CLOSE:
            return True

        # 下午时段: 13:00-16:00
        if cls.HK_AFTERNOON_OPEN <= current_time <= cls.HK_AFTERNOON_CLOSE:
            return True

        return False

    @classmethod
    def _is_us_trading_hours(cls, dt: datetime) -> bool:
        """检查是否在美股交易时间"""
        # 排除周末
        if dt.weekday() >= 5:  # 5=周六, 6=周日
            return False

        current_time = dt.time()

        # 美股交易时段: 09:30-16:00 (美东时间)
        if cls.US_OPEN <= current_time <= cls.US_CLOSE:
            return True

        return False

    @classmethod
    def get_active_index_symbols(cls, all_symbols: str) -> str:
        """
        根据当前市场时段，返回应该监控的指数

        Args:
            all_symbols: 所有配置的指数，逗号分隔，如 "HSI.HK,SPY.US"

        Returns:
            当前活跃市场的指数符号
        """
        if not all_symbols:
            return ""

        market = cls.get_current_market()

        # 解析所有指数
        symbols = [s.strip() for s in all_symbols.split(',') if s.strip()]

        # 根据市场筛选
        if market == "HK":
            # 港股时段，只返回港股指数
            return ",".join([s for s in symbols if ".HK" in s])
        elif market == "US":
            # 美股时段，只返回美股指数
            return ",".join([s for s in symbols if ".US" in s])
        else:
            # 都不在交易时段，返回空（不发送通知）
            return ""

    @classmethod
    def get_market_name(cls, market: MarketType) -> str:
        """获取市场中文名称"""
        names = {
            "HK": "港股",
            "US": "美股",
            "NONE": "无"
        }
        return names.get(market, "未知")


__all__ = ["MarketHours", "MarketType"]
