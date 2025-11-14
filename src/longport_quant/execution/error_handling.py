"""订单错误处理工具"""

from typing import Optional


# 不可重试的错误关键词列表
NON_RETRYABLE_ERROR_KEYWORDS = [
    "Insufficient holdings",
    "Insufficient cash",
    "Insufficient buying power",
    "Market closed",
    "Market is closed",
    "Symbol suspended",
    "Trading halted",
    "Invalid symbol",
    "Symbol not found",
    "Order quantity exceeds limit",  # 手数倍数错误虽然可能修复，但在当前session不应重试
]


def is_retryable_error(error_message: str) -> bool:
    """
    判断错误是否可以重试

    Args:
        error_message: 错误信息

    Returns:
        bool: True=可重试, False=不可重试
    """
    if not error_message:
        return True  # 未知错误，允许重试

    error_lower = error_message.lower()

    # 检查是否包含不可重试的关键词
    for keyword in NON_RETRYABLE_ERROR_KEYWORDS:
        if keyword.lower() in error_lower:
            return False

    # 默认允许重试
    return True


def get_error_category(error_message: str) -> str:
    """
    获取错误类别

    Args:
        error_message: 错误信息

    Returns:
        str: 错误类别
    """
    if not error_message:
        return "unknown"

    error_lower = error_message.lower()

    if "insufficient holdings" in error_lower or "pending orders occupying" in error_lower:
        return "insufficient_holdings"
    elif "insufficient cash" in error_lower or "insufficient buying power" in error_lower:
        return "insufficient_cash"
    elif "market closed" in error_lower or "market is closed" in error_lower:
        return "market_closed"
    elif "suspended" in error_lower or "halted" in error_lower:
        return "trading_suspended"
    elif "lot size" in error_lower or "multiple" in error_lower:
        return "lot_size_error"
    elif "timeout" in error_lower:
        return "timeout"
    elif "network" in error_lower or "connection" in error_lower:
        return "network_error"
    else:
        return "other"


def should_notify_user(error_category: str) -> bool:
    """
    判断是否需要通知用户

    Args:
        error_category: 错误类别

    Returns:
        bool: True=需要通知, False=不需要
    """
    # 这些错误需要用户关注
    notify_categories = {
        "insufficient_holdings",
        "insufficient_cash",
        "lot_size_error",
        "trading_suspended",
    }

    return error_category in notify_categories
