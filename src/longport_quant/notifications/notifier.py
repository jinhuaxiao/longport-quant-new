"""Unified notification manager for multiple channels."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from .discord import DiscordNotifier
from .slack import SlackNotifier


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

        Args:
            message: The message text to send
            **kwargs: Additional parameters (channel-specific formatting)
        """
        if not self._slack and not self._discord:
            logger.debug("No notification channels configured; skipping message: {}", message)
            return

        tasks = []
        if self._slack:
            tasks.append(self._slack.send(message, **kwargs))
        if self._discord:
            tasks.append(self._discord.send(message, **kwargs))

        # Send to all channels concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log any errors
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                channel = "Slack" if i == 0 and self._slack else "Discord"
                logger.error(f"{channel} notification failed: {result}")


__all__ = ["MultiChannelNotifier"]
