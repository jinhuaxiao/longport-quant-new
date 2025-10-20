#!/usr/bin/env python3
"""测试价格计算功能"""

def calculate_order_price(side, current_price, bid_price=None, ask_price=None, atr=None):
    """
    智能计算下单价格
    """
    # 计算价格调整幅度（基于ATR或固定比例）
    if atr and current_price > 0:
        price_adjustment = min(atr * 0.1, current_price * 0.002)  # ATR的10%或0.2%，取较小值
    else:
        price_adjustment = current_price * 0.001  # 默认0.1%

    if side.upper() == "BUY":
        if bid_price and bid_price > 0:
            # 在买一价基础上加价，提高成交概率
            order_price = bid_price + price_adjustment
        else:
            # 使用当前价略微减价
            order_price = current_price - price_adjustment
    else:  # SELL
        if ask_price and ask_price > 0:
            # 在卖一价基础上减价，提高成交概率
            order_price = ask_price - price_adjustment
        else:
            # 使用当前价略微加价
            order_price = current_price + price_adjustment

    # 确保价格为正
    order_price = max(order_price, 0.01)

    # 价格取整（保留2位小数）
    order_price = round(order_price, 2)

    # 格式化买卖价格，处理None的情况
    bid_str = f"${bid_price:.2f}" if bid_price is not None else "N/A"
    ask_str = f"${ask_price:.2f}" if ask_price is not None else "N/A"

    print(
        f"📊 下单价格计算: "
        f"方向={side}, "
        f"当前价=${current_price:.2f}, "
        f"买一={bid_str}, "
        f"卖一={ask_str}, "
        f"下单价=${order_price:.2f}"
    )

    return order_price


# 测试用例
print("测试价格计算功能")
print("=" * 50)

# 测试1：买入，有买卖盘
print("\n测试1：买入，有买卖盘")
calculate_order_price("BUY", 14.75, bid_price=14.74, ask_price=14.76, atr=0.38)

# 测试2：买入，无买卖盘
print("\n测试2：买入，无买卖盘")
calculate_order_price("BUY", 14.75, bid_price=None, ask_price=None, atr=0.38)

# 测试3：卖出，有买卖盘
print("\n测试3：卖出，有买卖盘")
calculate_order_price("SELL", 14.75, bid_price=14.74, ask_price=14.76, atr=0.38)

# 测试4：卖出，无买卖盘
print("\n测试4：卖出，无买卖盘")
calculate_order_price("SELL", 14.75, bid_price=None, ask_price=None, atr=None)

# 测试5：边界情况 - 价格为0
print("\n测试5：边界情况 - bid_price为0")
calculate_order_price("BUY", 14.75, bid_price=0, ask_price=14.76)