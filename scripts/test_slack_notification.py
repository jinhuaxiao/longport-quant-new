#!/usr/bin/env python3
"""测试Slack通知功能"""

import asyncio
from longport_quant.config import get_settings
from longport_quant.notifications.slack import SlackNotifier
from loguru import logger


async def test_slack():
    """测试Slack通知"""
    settings = get_settings()

    logger.info("=" * 60)
    logger.info("测试Slack通知功能")
    logger.info("=" * 60)

    if not settings.slack_webhook_url:
        logger.warning("⚠️  未配置SLACK_WEBHOOK_URL，跳过测试")
        logger.info("\n配置方法:")
        logger.info("1. 在 .env 文件中添加:")
        logger.info("   SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL")
        logger.info("\n2. 或在 configs/settings.toml 中添加:")
        logger.info("   slack_webhook_url = \"https://hooks.slack.com/services/YOUR/WEBHOOK/URL\"")
        return

    logger.info(f"✅ Slack Webhook已配置")
    logger.info(f"   URL: {str(settings.slack_webhook_url)[:50]}...")

    async with SlackNotifier(settings.slack_webhook_url) as slack:
        logger.info("\n正在发送测试消息...")

        # 测试1: 简单文本消息
        await slack.send("✅ Slack通知功能测试 - 简单消息")
        logger.info("✅ 测试1: 简单消息已发送")
        await asyncio.sleep(1)

        # 测试2: 交易信号格式
        signal_message = (
            "🚀 *STRONG_BUY* 信号: AAPL.US\n\n"
            "💯 综合评分: *85/100*\n"
            "💵 当前价格: $254.43\n"
            "📊 RSI: 28.5 | MACD: 1.234\n"
            "📉 布林带位置: 15.2%\n"
            "📈 成交量比率: 2.3x\n"
            "🎯 止损: $240.00 (-5.7%)\n"
            "🎁 止盈: $270.00 (+6.1%)\n"
            "📌 趋势: bullish\n"
            "💡 原因: RSI超卖, 价格接近下轨, MACD金叉, 成交量放大"
        )
        await slack.send(signal_message)
        logger.info("✅ 测试2: 交易信号消息已发送")
        await asyncio.sleep(1)

        # 测试3: 订单执行格式
        order_message = (
            "📈 *开仓订单已提交*\n\n"
            "📋 订单ID: `test_order_12345`\n"
            "📊 标的: *AAPL.US*\n"
            "💯 类型: STRONG_BUY (评分: 85/100)\n"
            "📦 数量: 20股\n"
            "💵 价格: $254.43\n"
            "💰 总额: $5088.60\n"
            "🎯 止损位: $240.00 (-5.7%)\n"
            "🎁 止盈位: $270.00 (+6.1%)\n"
            "📌 ATR: $4.81"
        )
        await slack.send(order_message)
        logger.info("✅ 测试3: 订单消息已发送")
        await asyncio.sleep(1)

        # 测试4: 止损触发
        stoploss_message = (
            "🛑 *止损触发*: AAPL.US\n\n"
            "💵 入场价: $254.43\n"
            "💸 当前价: $240.00\n"
            "🎯 止损位: $240.00\n"
            "📉 盈亏: *-5.67%*\n"
            "⚠️ 将执行卖出操作"
        )
        await slack.send(stoploss_message)
        logger.info("✅ 测试4: 止损消息已发送")
        await asyncio.sleep(1)

        # 测试5: 止盈触发
        takeprofit_message = (
            "🎉 *止盈触发*: AAPL.US\n\n"
            "💵 入场价: $254.43\n"
            "💰 当前价: $270.00\n"
            "🎁 止盈位: $270.00\n"
            "📈 盈亏: *+6.12%*\n"
            "✅ 将执行卖出操作"
        )
        await slack.send(takeprofit_message)
        logger.info("✅ 测试5: 止盈消息已发送")
        await asyncio.sleep(1)

        # 测试6: 平仓订单
        close_message = (
            "✅ *平仓订单已提交*\n\n"
            "📋 订单ID: `test_close_12345`\n"
            "📊 标的: *AAPL.US*\n"
            "📝 原因: 止盈\n"
            "📦 数量: 20股\n"
            "💵 入场价: $254.43\n"
            "💰 平仓价: $270.00\n"
            "💹 盈亏: $311.40 (*+6.12%*)"
        )
        await slack.send(close_message)
        logger.info("✅ 测试6: 平仓消息已发送")

    logger.info("\n" + "=" * 60)
    logger.info("✅ 所有测试消息已发送!")
    logger.info("请检查你的Slack频道查看消息")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_slack())