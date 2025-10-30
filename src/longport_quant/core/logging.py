"""Logging configuration utilities."""

import sys
from pathlib import Path

from loguru import logger

from longport_quant.config.settings import Settings


def configure_logging(settings: Settings) -> None:
    """Configure loguru sinks based on settings."""

    logger.remove()
    level = settings.log_level.upper()

    # 配置控制台输出，使用 sys.stdout 确保正确的 UTF-8 编码
    logger.add(
        sink=sys.stdout,
        level=level,
        backtrace=True,
        diagnose=False,
        enqueue=True,
        colorize=True,  # 启用彩色输出
    )

    log_path = Path(settings.log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(log_path, level=level, rotation="1 day", retention="7 days", enqueue=True)

