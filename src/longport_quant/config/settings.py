"""Runtime configuration loaded from env variables or config files."""

import os
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import AnyHttpUrl, AnyUrl, Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import (
    DotEnvSettingsSource,
    EnvSettingsSource,
    InitSettingsSource,
    SecretsSettingsSource,
    TomlConfigSettingsSource,
)


class BackupOrderConfig(BaseSettings):
    """备份条件单智能决策配置"""

    # 全局开关
    enabled: bool = Field(True, alias="BACKUP_ORDERS_ENABLED")

    # 风险评估阈值 (0-100)
    risk_threshold: int = Field(60, alias="BACKUP_ORDERS_RISK_THRESHOLD")

    # 风险因素权重配置
    atr_weight: int = Field(40, alias="BACKUP_ORDERS_ATR_WEIGHT")
    price_weight: int = Field(20, alias="BACKUP_ORDERS_PRICE_WEIGHT")
    signal_weight: int = Field(20, alias="BACKUP_ORDERS_SIGNAL_WEIGHT")
    stop_loss_weight: int = Field(20, alias="BACKUP_ORDERS_STOP_LOSS_WEIGHT")

    # ATR风险阈值
    atr_ratio_high: float = Field(0.03, alias="BACKUP_ORDERS_ATR_RATIO_HIGH")
    atr_ratio_medium: float = Field(0.02, alias="BACKUP_ORDERS_ATR_RATIO_MEDIUM")
    atr_ratio_low: float = Field(0.015, alias="BACKUP_ORDERS_ATR_RATIO_LOW")

    # 信号强度阈值
    weak_signal_threshold: int = Field(60, alias="BACKUP_ORDERS_WEAK_SIGNAL_THRESHOLD")

    # 止损幅度阈值
    wide_stop_loss_pct: float = Field(0.05, alias="BACKUP_ORDERS_WIDE_STOP_LOSS_PCT")

    # 跟踪止损配置（🔥 调整：2%→5%，减少误触发）
    use_trailing_stop: bool = Field(True, alias="BACKUP_ORDERS_USE_TRAILING_STOP")
    trailing_stop_percent: float = Field(0.05, alias="BACKUP_ORDERS_TRAILING_STOP_PERCENT")  # 5% (从2%提高)
    trailing_stop_limit_offset: float = Field(0.005, alias="BACKUP_ORDERS_TRAILING_STOP_LIMIT_OFFSET")  # 0.5%
    trailing_stop_expire_days: int = Field(7, alias="BACKUP_ORDERS_TRAILING_STOP_EXPIRE_DAYS")  # GTD 7天

    # 跟踪止盈配置（"让利润奔跑"策略，🔥 调整：6%→10%）
    use_trailing_profit: bool = Field(True, alias="BACKUP_ORDERS_USE_TRAILING_PROFIT")
    trailing_profit_percent: float = Field(0.10, alias="BACKUP_ORDERS_TRAILING_PROFIT_PERCENT")  # 10% (从6%提高)
    trailing_profit_limit_offset: float = Field(0.005, alias="BACKUP_ORDERS_TRAILING_PROFIT_LIMIT_OFFSET")  # 0.5%
    trailing_profit_expire_days: int = Field(7, alias="BACKUP_ORDERS_TRAILING_PROFIT_EXPIRE_DAYS")  # GTD 7天

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")


class Settings(BaseSettings):
    """Application level settings."""

    # 账号标识（用于多账号支持）
    account_id: str | None = Field(None, alias="ACCOUNT_ID")

    # 类变量：存储当前账号ID用于配置源判断
    _account_id_for_settings: str | None = None

    environment: str = Field("development", alias="ENVIRONMENT")
    timezone: str = Field("Asia/Hong_Kong", alias="TRADING_TIMEZONE")

    # Longport API 配置
    longport_api_base: AnyHttpUrl = Field(
        "https://openapi.longportapp.com", alias="LONGPORT_API_BASE"
    )
    longport_ws_url: AnyUrl = Field(
        "wss://openapi-quote.longportapp.com/v1/quote", alias="LONGPORT_WS_URL"
    )
    longport_trade_ws_url: AnyUrl = Field(
        "wss://openapi-trade.longportapp.com/v1/trade", alias="LONGPORT_TRADE_WS_URL"
    )

    # Longport API 凭证（直接在 Settings 类中，支持账号特定配置）
    longport_app_key: str = Field(..., alias="LONGPORT_APP_KEY")
    longport_app_secret: str = Field(..., alias="LONGPORT_APP_SECRET")
    longport_access_token: str | None = Field(None, alias="LONGPORT_ACCESS_TOKEN")
    longport_region: str = Field("hk", alias="LONGPORT_REGION")

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

    # 批量信号处理配置（智能混合模式 - 高分信号优先）
    signal_batch_window: float = Field(15.0, alias="SIGNAL_BATCH_WINDOW")  # 等待15秒收集信号
    signal_batch_size: int = Field(5, alias="SIGNAL_BATCH_SIZE")  # 每批最多5个信号
    stop_loss_priority: int = Field(999, alias="STOP_LOSS_PRIORITY")  # 止损止盈优先级（立即执行）
    min_signal_score: int = Field(40, alias="MIN_SIGNAL_SCORE")  # 最低分数阈值
    funds_retry_max: int = Field(5, alias="FUNDS_RETRY_MAX")  # 资金不足最大重试次数
    funds_retry_delay: int = Field(5, alias="FUNDS_RETRY_DELAY")  # 资金不足重试延迟（分钟）

    # 信号过期和队列优化配置
    signal_ttl_seconds: int = Field(3600, alias="SIGNAL_TTL_SECONDS")  # 信号过期时间（秒，默认1小时）
    max_delay_seconds: int = Field(1800, alias="MAX_DELAY_SECONDS")  # 延迟信号最大等待时间（秒，默认30分钟）
    empty_queue_sleep: float = Field(10.0, alias="EMPTY_QUEUE_SLEEP")  # 队列为空时休眠时间（秒）

    watchlist_path: Path = Field(Path("configs/watchlist.yml"), alias="WATCHLIST_PATH")
    strategy_modules: List[str] = Field(default_factory=list, alias="STRATEGY_MODULES")
    active_markets: List[str] = Field(default_factory=list, alias="ACTIVE_MARKETS")

    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_path: Path = Field(Path("logs/app.log"), alias="LOG_PATH")

    health_port: int = Field(8080, alias="HEALTHCHECK_PORT")
    slack_webhook_url: HttpUrl | None = Field(None, alias="SLACK_WEBHOOK_URL")
    discord_webhook_url: HttpUrl | None = Field(None, alias="DISCORD_WEBHOOK_URL")

    # 备份条件单配置
    backup_orders: BackupOrderConfig = Field(default_factory=BackupOrderConfig)

    # 🚫 防止频繁交易配置
    enable_reentry_cooldown: bool = Field(True, alias="ENABLE_REENTRY_COOLDOWN")  # 启用卖出后再买入冷却期
    reentry_cooldown: int = Field(10800, alias="REENTRY_COOLDOWN")  # 卖出后再买入冷却期（秒，默认3小时=10800）
    enable_min_holding_period: bool = Field(True, alias="ENABLE_MIN_HOLDING_PERIOD")  # 启用最小持仓时间
    min_holding_period: int = Field(1800, alias="MIN_HOLDING_PERIOD")  # 最小持仓时间（秒，默认30分钟=1800）
    enable_signal_confirmation: bool = Field(False, alias="ENABLE_SIGNAL_CONFIRMATION")  # 启用信号确认机制（可选）
    signal_confirmation_count: int = Field(2, alias="SIGNAL_CONFIRMATION_COUNT")  # 信号确认次数
    enable_transaction_cost_penalty: bool = Field(True, alias="ENABLE_TRANSACTION_COST_PENALTY")  # 启用交易成本惩罚
    transaction_cost_pct: float = Field(0.002, alias="TRANSACTION_COST_PCT")  # 交易成本比例（0.2%）

    # 信号冷却期（秒）：同一标的在该时间窗口内不重复生成新信号
    signal_cooldown_seconds: int = Field(900, alias="SIGNAL_COOLDOWN_SECONDS")

    # 每日交易次数上限（可选开关）
    enable_daily_trade_cap: bool = Field(False, alias="ENABLE_DAILY_TRADE_CAP")
    daily_max_buy_orders: int = Field(8, alias="DAILY_MAX_BUY_ORDERS")
    daily_max_sell_orders: int = Field(8, alias="DAILY_MAX_SELL_ORDERS")

    # 单标的每日买入上限（可选，默认1次）；仅对普通BUY生效，强制止损/止盈不受限
    enable_per_symbol_daily_cap: bool = Field(False, alias="ENABLE_PER_SYMBOL_DAILY_CAP")
    per_symbol_daily_max_buys: int = Field(1, alias="PER_SYMBOL_DAILY_MAX_BUYS")

    # 最小下单金额（小额订单过滤，单位：各币种）
    min_order_notional_hkd: float = Field(0.0, alias="MIN_ORDER_NOTIONAL_HKD")
    min_order_notional_usd: float = Field(0.0, alias="MIN_ORDER_NOTIONAL_USD")

    # 软退出引擎配置（Chandelier/Donchian）
    soft_exit_enabled: bool = Field(False, alias="SOFT_EXIT_ENABLED")
    soft_exit_period: str = Field("Min_5", alias="SOFT_EXIT_PERIOD")  # 分时周期：Min_1/Min_5/Day 等
    soft_exit_atr_period: int = Field(22, alias="SOFT_EXIT_ATR_PERIOD")
    soft_exit_chandelier_k: float = Field(3.0, alias="SOFT_EXIT_CHANDELIER_K")
    soft_exit_donchian_n: int = Field(20, alias="SOFT_EXIT_DONCHIAN_N")
    soft_exit_poll_interval: int = Field(30, alias="SOFT_EXIT_POLL_INTERVAL")  # 秒
    soft_exit_signal_cooldown: int = Field(600, alias="SOFT_EXIT_SIGNAL_COOLDOWN")  # 同一标的冷却（秒）

    # 市场状态与仓位/购买力调度（Regime Engine）
    regime_enabled: bool = Field(False, alias="REGIME_ENABLED")
    regime_index_symbols: str = Field("HSI.HK", alias="REGIME_INDEX_SYMBOLS")  # 逗号分隔，如 "HSI.HK,SPY.US"
    regime_ma_period: int = Field(200, alias="REGIME_MA_PERIOD")
    regime_update_interval_minutes: int = Field(10, alias="REGIME_UPDATE_INTERVAL_MINUTES")
    # 各状态购买力保留比例（预留不出手的现金）
    regime_reserve_pct_bull: float = Field(0.15, alias="REGIME_RESERVE_PCT_BULL")
    regime_reserve_pct_range: float = Field(0.30, alias="REGIME_RESERVE_PCT_RANGE")
    regime_reserve_pct_bear: float = Field(0.50, alias="REGIME_RESERVE_PCT_BEAR")
    # 各状态仓位缩放（对动态预算的乘数，控制进攻/防守）
    regime_position_scale_bull: float = Field(1.0, alias="REGIME_POSITION_SCALE_BULL")
    regime_position_scale_range: float = Field(0.70, alias="REGIME_POSITION_SCALE_RANGE")
    regime_position_scale_bear: float = Field(0.40, alias="REGIME_POSITION_SCALE_BEAR")

    # 日内风格（当日：趋势/震荡）
    intraday_style_enabled: bool = Field(False, alias="INTRADAY_STYLE_ENABLED")
    intraday_update_interval_minutes: int = Field(3, alias="INTRADAY_UPDATE_INTERVAL_MINUTES")
    intraday_open_minutes: int = Field(30, alias="INTRADAY_OPEN_MINUTES")
    intraday_trend_expand_threshold: float = Field(2.0, alias="INTRADAY_TREND_EXPAND_THRESHOLD")  # 当日范围/开盘区间
    intraday_breakout_buffer_pct: float = Field(0.002, alias="INTRADAY_BREAKOUT_BUFFER_PCT")  # 0.2%
    intraday_scale_trend: float = Field(1.10, alias="INTRADAY_SCALE_TREND")
    intraday_scale_range: float = Field(0.85, alias="INTRADAY_SCALE_RANGE")
    intraday_reserve_delta_trend: float = Field(-0.05, alias="INTRADAY_RESERVE_DELTA_TREND")
    intraday_reserve_delta_range: float = Field(0.05, alias="INTRADAY_RESERVE_DELTA_RANGE")

    # 去杠杆调仓（Regime Rebalancer）
    rebalancer_enabled: bool = Field(False, alias="REBALANCER_ENABLED")
    rebalancer_min_interval_minutes: int = Field(30, alias="REBALANCER_MIN_INTERVAL_MINUTES")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    def __init__(self, **kwargs):
        """初始化Settings，支持从account_id加载配置"""
        # 如果指定了account_id，存储到类变量供settings_customise_sources使用
        account_id = kwargs.get("account_id") or os.getenv("ACCOUNT_ID")
        if account_id:
            # 存储account_id用于配置源判断（不再修改model_config）
            self.__class__._account_id_for_settings = account_id

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
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: InitSettingsSource,
        env_settings: EnvSettingsSource,
        dotenv_settings: DotEnvSettingsSource,
        file_secret_settings: SecretsSettingsSource,
    ):
        """
        自定义配置源优先级，实现配置继承：
        1. init_settings（初始化参数）- 最高优先级
        2. account_dotenv（账号特定配置，如 paper_001.env）- 覆盖全局配置
        3. env_settings（环境变量）
        4. dotenv_settings（全局 .env 文件）- 默认值
        5. toml_settings（TOML 配置文件）
        6. file_secret_settings（secrets 文件）- 最低优先级
        """
        # 基础配置源列表
        sources = [init_settings]

        # 如果有 account_id，添加账号特定配置源（优先级高于全局配置）
        if cls._account_id_for_settings:
            account_env_file = Path(f"configs/accounts/{cls._account_id_for_settings}.env")
            if account_env_file.exists():
                account_dotenv = DotEnvSettingsSource(
                    settings_cls,
                    env_file=str(account_env_file),
                    env_file_encoding="utf-8",
                )
                sources.append(account_dotenv)

        # 添加环境变量配置源
        sources.append(env_settings)

        # 添加全局 .env 配置源（作为默认值）
        base_dotenv = DotEnvSettingsSource(
            settings_cls,
            env_file=".env",
            env_file_encoding="utf-8",
        )
        sources.append(base_dotenv)

        # 添加 TOML 配置源
        toml_file = Path("configs/settings.toml")
        if toml_file.exists():
            toml_settings = TomlConfigSettingsSource(settings_cls, toml_file=toml_file)
            sources.append(toml_settings)

        # 添加 secrets 配置源
        sources.append(file_secret_settings)

        return tuple(sources)


def get_settings(account_id: str | None = None) -> Settings:
    """
    Return settings instance.

    Args:
        account_id: 账号ID，如果指定则从configs/accounts/{account_id}.env加载配置

    Returns:
        Settings instance
    """
    return Settings(account_id=account_id)  # type: ignore[call-arg]


__all__ = ["Settings", "get_settings", "BackupOrderConfig"]
