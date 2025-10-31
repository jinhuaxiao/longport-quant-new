#!/usr/bin/env python3
"""测试 Discord 和多渠道通知功能"""

import asyncio
from longport_quant.config import get_settings
from longport_quant.notifications import MultiChannelNotifier, DiscordNotifier
from loguru import logger


async def test_discord_only():
    """测试纯 Discord 通知"""
    logger.info("\n=== 测试1: 纯Discord通知 ===")
    settings = get_settings()

    if not settings.discord_webhook_url:
        logger.error("❌ Discord Webhook未配置")
        logger.info("请在 .env 文件中设置 DISCORD_WEBHOOK_URL")
        return

    logger.info(f"✅ Discord Webhook已配置")
    logger.info(f"   URL: {str(settings.discord_webhook_url)[:50]}...")

    async with DiscordNotifier(settings.discord_webhook_url) as discord:
        logger.info("\n正在发送测试消息...")

        # 测试1: 简单文本消息
        await discord.send("🧪 Discord通知测试 - 简单消息")
        logger.info("✅ 测试1完成: 简单消息")
        await asyncio.sleep(1)

        # 测试2: 带中文的消息
        await discord.send("📊 交易信号测试\n标的: AAPL.US\n操作: 买入\n价格: $150.00")
        logger.info("✅ 测试2完成: 带中文消息")
        await asyncio.sleep(1)

        # 测试3: emoji消息
        await discord.send("🚀 突破信号 | 💰 止盈提醒 | ⚠️ 风险警告")
        logger.info("✅ 测试3完成: Emoji消息")

    logger.info("\n✅ 所有Discord测试完成！")


async def test_multi_channel():
    """测试多渠道通知（Slack + Discord）"""
    logger.info("\n=== 测试2: 多渠道通知（Slack + Discord） ===")
    settings = get_settings()

    slack_url = str(settings.slack_webhook_url) if settings.slack_webhook_url else None
    discord_url = str(settings.discord_webhook_url) if settings.discord_webhook_url else None

    if not slack_url and not discord_url:
        logger.error("❌ 未配置任何通知渠道")
        return

    logger.info(f"配置状态:")
    logger.info(f"  Slack: {'✅' if slack_url else '❌'}")
    logger.info(f"  Discord: {'✅' if discord_url else '❌'}")

    async with MultiChannelNotifier(slack_webhook_url=slack_url, discord_webhook_url=discord_url) as notifier:
        logger.info("\n正在发送多渠道测试消息...")

        # 测试1: 简单通知
        await notifier.send("🧪 多渠道通知测试 - 同时发送到Slack和Discord")
        logger.info("✅ 测试1完成: 简单通知")
        await asyncio.sleep(1)

        # 测试2: 交易信号通知
        signal_msg = """
📈 交易信号
━━━━━━━━━━━━━━━━
标的: TSLA.US
操作: 买入
价格: $240.50
数量: 100股
策略: 动量突破
信号强度: 85/100
        """.strip()
        await notifier.send(signal_msg)
        logger.info("✅ 测试2完成: 交易信号")
        await asyncio.sleep(1)

        # 测试3: 风险警告
        await notifier.send("⚠️ 风险警告: 账户可用资金低于安全阈值")
        logger.info("✅ 测试3完成: 风险警告")

    logger.info("\n✅ 所有多渠道测试完成！")


async def main():
    logger.info("=" * 50)
    logger.info("开始通知系统测试")
    logger.info("=" * 50)

    try:
        # 测试1: Discord单独测试
        await test_discord_only()
        await asyncio.sleep(2)

        # 测试2: 多渠道测试
        await test_multi_channel()

    except Exception as e:
        logger.error(f"测试失败: {e}")
        import traceback
        traceback.print_exc()

    logger.info("\n" + "=" * 50)
    logger.info("测试完成")
    logger.info("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
