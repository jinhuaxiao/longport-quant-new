"""Slack notification helper."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import httpx
from loguru import logger


class SlackNotifier:
    """Thin async wrapper for posting messages to Slack via webhook."""

    def __init__(self, webhook_url: str | None) -> None:
        # Convert HttpUrl to string if needed
        self._webhook_url = str(webhook_url) if webhook_url else None
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

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
            except Exception as exc:  # pragma: no cover - network failure
                logger.error("Slack notification failed: {}", exc)


__all__ = ["SlackNotifier"]
