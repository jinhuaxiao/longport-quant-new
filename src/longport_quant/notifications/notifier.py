"""Unified notification manager for multiple channels."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from .discord import DiscordNotifier
from .slack import SlackNotifier, SlackRateLimitError


class MultiChannelNotifier:
    """Unified notifier that sends messages to multiple channels (Slack, Discord, etc.)."""

    def __init__(
        self,
        slack_webhook_url: str | None = None,
        discord_webhook_url: str | None = None,
    ) -> None:
        self._slack = SlackNotifier(slack_webhook_url) if slack_webhook_url else None
        self._discord = DiscordNotifier(discord_webhook_url) if discord_webhook_url else None

        # Log which channels are enabled
        channels = []
        if self._slack:
            channels.append("Slack")
        if self._discord:
            channels.append("Discord")

        if channels:
            logger.info(f"✅ 通知已启用: {', '.join(channels)}")
        else:
            logger.warning("⚠️ 未配置任何通知渠道")

    async def __aenter__(self) -> "MultiChannelNotifier":
        """Enter async context manager."""
        if self._slack:
            await self._slack.__aenter__()
        if self._discord:
            await self._discord.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """Exit async context manager."""
        tasks = []
        if self._slack:
            tasks.append(self._slack.__aexit__(exc_type, exc, tb))
        if self._discord:
            tasks.append(self._discord.__aexit__(exc_type, exc, tb))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def send(self, message: str, **kwargs: Any) -> None:
        """
        Send a message to all configured notification channels.

        自动故障转移：如果Slack遇到429限流，自动切换到Discord。

        Args:
            message: The message text to send
            **kwargs: Additional parameters (channel-specific formatting)
        """
        if not self._slack and not self._discord:
            logger.debug("No notification channels configured; skipping message: {}", message)
            return

        # 🔥 尝试Slack，遇到429自动切换到Discord
        slack_failed = False
        slack_rate_limited = False

        if self._slack:
            try:
                await self._slack.send(message, **kwargs)
                logger.debug("✅ 消息已发送到 Slack")
                return  # 🔥 成功发送，直接返回
            except SlackRateLimitError as e:
                # 🔥 Slack限流，记录并切换到Discord
                slack_rate_limited = True
                slack_failed = True
                logger.info("⚠️ Slack限流，自动切换到Discord")
            except Exception as e:
                # 🔥 其他Slack错误
                slack_failed = True
                logger.warning(f"⚠️ Slack发送失败: {e}")

        # 🔥 如果Slack失败/限流/未配置，使用Discord作为备用
        if self._discord:
            try:
                # 🔥 如果是Slack限流，在Discord消息中标注
                prefix = "⚠️ [Slack限流，使用Discord备用通道]\n\n" if slack_rate_limited else ""
                await self._discord.send(prefix + message, **kwargs)
                logger.debug("✅ 消息已发送到 Discord")
            except Exception as e:
                logger.error(f"❌ Discord发送失败: {e}")


__all__ = ["MultiChannelNotifier"]
