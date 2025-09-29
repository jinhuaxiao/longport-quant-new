"""Logging configuration utilities."""

from pathlib import Path

from loguru import logger

from longport_quant.config.settings import Settings


def configure_logging(settings: Settings) -> None:
    """Configure loguru sinks based on settings."""

    logger.remove()
    level = settings.log_level.upper()
    logger.add(
        sink=lambda msg: print(msg, end=""),
        level=level,
        backtrace=True,
        diagnose=False,
        enqueue=True,
    )

    log_path = Path(settings.log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(log_path, level=level, rotation="1 day", retention="7 days", enqueue=True)

