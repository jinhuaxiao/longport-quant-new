"""Runtime configuration loaded from env variables or config files."""

import os
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import AnyHttpUrl, AnyUrl, Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import TomlConfigSettingsSource


class LongportCredentials(BaseSettings):
    app_key: str = Field(..., alias="LONGPORT_APP_KEY")
    app_secret: str = Field(..., alias="LONGPORT_APP_SECRET")
    access_token: str | None = Field(None, alias="LONGPORT_ACCESS_TOKEN")
    region: str = Field("hk", alias="LONGPORT_REGION")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class Settings(BaseSettings):
    """Application level settings."""

    # 账号标识（用于多账号支持）
    account_id: str | None = Field(None, alias="ACCOUNT_ID")

    environment: str = Field("development", alias="ENVIRONMENT")
    timezone: str = Field("Asia/Hong_Kong", alias="TRADING_TIMEZONE")

    longport_api_base: AnyHttpUrl = Field(
        "https://openapi.longportapp.com", alias="LONGPORT_API_BASE"
    )
    longport_ws_url: AnyUrl = Field(
        "wss://openapi-quote.longportapp.com/v1/quote", alias="LONGPORT_WS_URL"
    )
    longport_trade_ws_url: AnyUrl = Field(
        "wss://openapi-trade.longportapp.com/v1/trade", alias="LONGPORT_TRADE_WS_URL"
    )
    longport_credentials: LongportCredentials = Field(default_factory=LongportCredentials)

    database_dsn: str = Field(
        "postgresql+asyncpg://user:password@localhost:5432/longport",
        alias="DATABASE_DSN",
    )
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")

    # 信号队列配置（用于解耦信号生成和订单执行）
    signal_queue_key: str = Field("trading:signals", alias="SIGNAL_QUEUE_KEY")
    signal_processing_key: str = Field("trading:signals:processing", alias="SIGNAL_PROCESSING_KEY")
    signal_failed_key: str = Field("trading:signals:failed", alias="SIGNAL_FAILED_KEY")
    signal_max_retries: int = Field(3, alias="SIGNAL_MAX_RETRIES")
    signal_queue_max_size: int = Field(1000, alias="SIGNAL_QUEUE_MAX_SIZE")
    order_executor_workers: int = Field(1, alias="ORDER_EXECUTOR_WORKERS")

    watchlist_path: Path = Field(Path("configs/watchlist.yml"), alias="WATCHLIST_PATH")
    strategy_modules: List[str] = Field(default_factory=list, alias="STRATEGY_MODULES")
    active_markets: List[str] = Field(default_factory=list, alias="ACTIVE_MARKETS")

    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_path: Path = Field(Path("logs/app.log"), alias="LOG_PATH")

    health_port: int = Field(8080, alias="HEALTHCHECK_PORT")
    slack_webhook_url: HttpUrl | None = Field(None, alias="SLACK_WEBHOOK_URL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    def __init__(self, **kwargs):
        """初始化Settings，支持从account_id加载配置"""
        # 如果指定了account_id，优先从configs/accounts/{account_id}.env加载
        account_id = kwargs.get("account_id") or os.getenv("ACCOUNT_ID")
        if account_id:
            account_env_file = Path(f"configs/accounts/{account_id}.env")
            if account_env_file.exists():
                # 临时修改model_config的env_file
                self.__class__.model_config["env_file"] = str(account_env_file)

        super().__init__(**kwargs)

        # 如果有account_id，自动为队列key添加后缀以实现隔离
        if self.account_id:
            self.signal_queue_key = f"{self.signal_queue_key}:{self.account_id}"
            self.signal_processing_key = f"{self.signal_processing_key}:{self.account_id}"
            self.signal_failed_key = f"{self.signal_failed_key}:{self.account_id}"

    @field_validator("database_dsn", mode="before")
    @classmethod
    def _normalise_postgres_dsn(cls, value: str) -> str:
        if isinstance(value, str) and value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value[len("postgres://") :]
        return value

    @classmethod
    def settings_customise_sources(cls, *args, **kwargs):
        init_settings = kwargs.get("init_settings", args[0] if len(args) > 0 else None)
        env_settings = kwargs.get("env_settings", args[1] if len(args) > 1 else None)
        dotenv_settings = kwargs.get("dotenv_settings", args[2] if len(args) > 2 else None)
        file_secret_settings = kwargs.get("file_secret_settings", args[3] if len(args) > 3 else None)
        toml_settings = TomlConfigSettingsSource(cls, toml_file="configs/settings.toml")
        return (
            init_settings,
            toml_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )


def get_settings(account_id: str | None = None) -> Settings:
    """
    Return settings instance.

    Args:
        account_id: 账号ID，如果指定则从configs/accounts/{account_id}.env加载配置

    Returns:
        Settings instance
    """
    return Settings(account_id=account_id)  # type: ignore[call-arg]


__all__ = ["Settings", "get_settings", "LongportCredentials"]
