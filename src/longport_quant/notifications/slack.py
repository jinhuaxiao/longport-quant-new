"""Slack notification helper."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict

import httpx
from loguru import logger


class SlackRateLimitError(Exception):
    """Raised when Slack API returns 429 Too Many Requests."""
    pass


class SlackNotifier:
    """Thin async wrapper for posting messages to Slack via webhook."""

    def __init__(self, webhook_url: str | None) -> None:
        # Convert HttpUrl to string if needed
        self._webhook_url = str(webhook_url) if webhook_url else None
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

        # 🔥 限流保护：记录上次429错误的时间
        self._last_429_time = 0.0
        self._cooldown_seconds = 60  # 429错误后冷却60秒

    async def __aenter__(self) -> "SlackNotifier":
        if self._webhook_url and not self._client:
            self._client = httpx.AsyncClient(timeout=10)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send(self, message: str, **kwargs: Any) -> None:
        """Send a message to Slack if webhook has been configured."""

        if not self._webhook_url:
            logger.debug(
                "Slack webhook not configured; skipping message: {}", message
            )
            return

        # 🔥 检查是否在冷却期内
        now = time.time()
        if self._last_429_time > 0 and (now - self._last_429_time) < self._cooldown_seconds:
            remaining = self._cooldown_seconds - (now - self._last_429_time)
            logger.debug(f"⏳ Slack 在冷却期内（还需{remaining:.0f}秒），跳过消息")
            raise SlackRateLimitError(f"Slack in cooldown ({remaining:.0f}s remaining)")

        # 清理Unicode字符以防止编码错误
        try:
            message = message.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
        except Exception:
            message = message.encode('ascii', errors='ignore').decode('ascii')

        payload: Dict[str, Any] = {"text": message}
        if kwargs:
            payload.update(kwargs)

        async with self._lock:
            client = self._client
            if client is None:
                client = httpx.AsyncClient(timeout=10)
                self._client = client
            try:
                response = await client.post(self._webhook_url, json=payload)
                response.raise_for_status()
                # 🔥 成功发送，清除429记录
                self._last_429_time = 0.0
            except httpx.HTTPStatusError as exc:
                # 🔥 检查是否是429错误
                if exc.response.status_code == 429:
                    self._last_429_time = time.time()
                    logger.warning(
                        f"⚠️ Slack API 限流（429），{self._cooldown_seconds}秒内自动切换到备用通道"
                    )
                    raise SlackRateLimitError("Slack API rate limit exceeded") from exc
                else:
                    logger.error("Slack notification failed: {}", exc)
                    raise
            except Exception as exc:  # pragma: no cover - network failure
                logger.error("Slack notification failed: {}", exc)
                raise


__all__ = ["SlackNotifier", "SlackRateLimitError"]
