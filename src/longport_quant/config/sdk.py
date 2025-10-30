"""Helpers for constructing Longport SDK configuration."""

from __future__ import annotations

import os

from loguru import logger
from longport import OpenApiException, openapi

from longport_quant.config.settings import Settings


def build_sdk_config(settings: Settings) -> openapi.Config:
    """Create a reusable SDK Config from application settings via SDK helpers."""

    overrides = {
        "LONGPORT_APP_KEY": settings.longport_app_key,
        "LONGPORT_APP_SECRET": settings.longport_app_secret,
        "LONGPORT_ACCESS_TOKEN": settings.longport_access_token or "",
    }

    for env_key, value in overrides.items():
        if value:
            os.environ[env_key] = value

    try:
        config = openapi.Config.from_env()
    except OpenApiException as exc:  # pragma: no cover - network errors
        logger.error("Failed to construct SDK config: {}", exc)
        raise

    if not settings.longport_access_token:
        try:
            logger.info("Refreshing Longport access token via SDK")
            config.refresh_access_token()
        except OpenApiException as exc:  # pragma: no cover - network errors
            logger.error("Failed to refresh Longport access token: {}", exc)
            raise

    return config


__all__ = ["build_sdk_config"]
