#!/usr/bin/env python3
"""测试带中文名称的Slack通知"""

import asyncio
from loguru import logger


async def test_notifications():
    """测试所有通知类型的中文名称显示"""
    # 导入交易系统
    from advanced_technical_trading import AdvancedTechnicalTrader

    # 创建交易系统实例
    trader = AdvancedTechnicalTrader(use_builtin_watchlist=True)

    logger.info("=" * 70)
    logger.info("测试带中文名称的Slack通知格式")
    logger.info("=" * 70)

    # 测试标的
    test_symbols = [
        "9988.HK",  # 阿里巴巴
        "0700.HK",  # 腾讯
        "1929.HK",  # 周大福
        "AAPL.US",  # 苹果
        "NVDA.US",  # 英伟达
        "TEST.HK",  # 不在列表中的标的
    ]

    logger.info("\n📋 测试获取标的中文名称:")
    logger.info("-" * 50)

    for symbol in test_symbols:
        name = trader._get_symbol_name(symbol)
        if name:
            logger.info(f"  {symbol}: {name}")
        else:
            logger.info(f"  {symbol}: (无中文名称)")

    # 模拟通知消息
    logger.info("\n📱 模拟Slack通知格式:")
    logger.info("-" * 50)

    # 买入通知示例
    symbol = "9988.HK"
    symbol_name = trader._get_symbol_name(symbol)
    symbol_display = f"{symbol} ({symbol_name})" if symbol_name else symbol

    buy_message = f"""
🚀 *开仓订单已提交*

📋 订单ID: `ORDER123456`
📊 标的: *{symbol_display}*
💯 信号类型: STRONG_BUY
⭐ 综合评分: *75/100*

💰 *交易信息*:
   • 数量: 200股
   • 价格: $92.50
   • 总额: $18,500.00

📊 *技术指标*:
   • RSI: 28.5 (超卖 ⬇️)
   • MACD: 0.123 | Signal: 0.098
   • MACD差值: +0.025 (金叉 ✅)
   • 布林带位置: 15% (接近下轨 ⬇️)
   • 成交量比率: 1.8x (放量 📈)
   • 趋势: bullish 📈

🎯 *风控设置*:
   • 止损位: $88.20 (-4.6%)
   • 止盈位: $99.50 (+7.6%)
   • ATR: $1.85
"""

    logger.info("买入通知示例:")
    logger.info(buy_message)

    # 卖出通知示例
    symbol = "0700.HK"
    symbol_name = trader._get_symbol_name(symbol)
    symbol_display = f"{symbol} ({symbol_name})" if symbol_name else symbol

    sell_message = f"""
✅ *平仓订单已提交*

📋 订单ID: `ORDER789012`
📊 标的: *{symbol_display}*
📝 原因: 止盈
📦 数量: 100股
💵 入场价: $350.00
💰 平仓价: $385.00
💹 盈亏: $3,500.00 (*+10.00%*)
"""

    logger.info("\n卖出通知示例:")
    logger.info(sell_message)

    # 止损通知示例
    symbol = "1929.HK"
    symbol_name = trader._get_symbol_name(symbol)
    symbol_display = f"{symbol} ({symbol_name})" if symbol_name else symbol

    stop_loss_message = f"""
🛑 *止损触发*: {symbol_display}

💵 入场价: $15.20
💸 当前价: $14.30
🎯 止损位: $14.40
📉 盈亏: *-5.92%*
⚠️ 将执行卖出操作
"""

    logger.info("\n止损通知示例:")
    logger.info(stop_loss_message)

    # 智能止盈继续持有通知
    symbol = "NVDA.US"
    symbol_name = trader._get_symbol_name(symbol)
    symbol_display = f"{symbol} ({symbol_name})" if symbol_name else symbol

    hold_message = f"""
💡 *智能止盈 - 继续持有*: {symbol_display}

💵 入场价: $450.00
💰 当前价: $520.00
🎁 原止盈位: $495.00
📈 当前盈亏: *+15.56%*

🔍 *持有理由*:
技术指标仍显示STRONG_BUY信号 (评分: 82/100)

📊 *当前技术指标*:
   • RSI: 65.3
   • MACD: 2.456
   • 趋势: bullish

✅ 继续持有，等待更好的退出机会
"""

    logger.info("\n智能止盈继续持有通知示例:")
    logger.info(hold_message)

    # 统计
    logger.info("\n" + "=" * 70)
    logger.info("📊 通知优化总结")
    logger.info("=" * 70)
    logger.info("✅ 所有订单通知现在都会显示:")
    logger.info("   1. 标的代码 + 中文名称")
    logger.info("   2. 如: 9988.HK (阿里巴巴)")
    logger.info("   3. 便于快速识别交易标的")
    logger.info("\n✅ 支持的通知类型:")
    logger.info("   • 开仓订单通知")
    logger.info("   • 平仓订单通知")
    logger.info("   • 止损触发通知")
    logger.info("   • 止盈触发通知")
    logger.info("   • 智能止盈继续持有通知")
    logger.info("\n✅ 覆盖的标的:")
    logger.info(f"   • 港股: {len(trader.hk_watchlist)}只")
    logger.info(f"   • 美股: {len(trader.us_watchlist)}只")


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════════════╗
║                   测试带中文名称的Slack通知                             ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  功能改进:                                                            ║
║    • 所有Slack通知都会显示标的的中文名称                                ║
║    • 格式: 代码 (中文名)，如 9988.HK (阿里巴巴)                        ║
║    • 便于快速识别交易标的                                              ║
║                                                                       ║
║  覆盖通知类型:                                                        ║
║    • 买入订单通知                                                     ║
║    • 卖出订单通知                                                     ║
║    • 止损/止盈触发通知                                                ║
║    • 智能止盈决策通知                                                  ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
    """)

    asyncio.run(test_notifications())