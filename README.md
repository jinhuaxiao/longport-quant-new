# Longport Quant Trading Framework

This repository provides a Python scaffold for building fully automated trading strategies that connect to the Longport OpenAPI and persist data to PostgreSQL. The focus is a curated watchlist of Hong Kong and US equities, rather than full-market scanning.

## Features
- Async microservice-style modules for market data, strategy execution, risk controls, and order routing.
- Centralised configuration with hot-reload support for ticker watchlists and environment settings.
- PostgreSQL persistence via SQLAlchemy models and alembic migrations.
- Structured logging, metrics hooks, and ops utilities for monitoring and alerting.
- Templates for strategy development, backtesting hooks, and deployment scripts.

## Getting Started
1. Copy `configs/settings.example.toml` to `configs/settings.toml` and adjust API credentials, database DSN, and runtime options.
2. Copy `configs/watchlist.example.yml` to `configs/watchlist.yml` and list the tickers to monitor.
3. Create a Python virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```
4. Run the development orchestrator:
   ```bash
   python -m longport_quant.core.app
   ```

## Watchlist Auto Trading
- Populate `configs/watchlist.yml` with the Hong Kong and US symbols you want to auto-trade, then sync them into PostgreSQL via `python scripts/ingest_watchlist.py`.
- Enable the built-in strategy by setting `STRATEGY_MODULES = ["longport_quant.strategies.watchlist_auto.AutoTradeStrategy"]` in your `.env` or `configs/settings.toml`.
- Start the runtime with `python scripts/run_strategy.py`; the strategy will monitor the incoming quotes and place one BUY order per symbol during the appropriate local trading window.
- Adjust per-market budgets and trading hours by editing `AutoTradeStrategy` (see `src/longport_quant/strategies/watchlist_auto.py`).

### Longport SDK 接入
- 框架使用官方 `longport` Python SDK 创建 `QuoteContext` 与 `TradeContext`，自动处理认证、心跳与推送；行情推送会被转换为 `quote` 事件分发给策略。
- `.env` 需提供 `LONGPORT_APP_KEY`、`LONGPORT_APP_SECRET`、`LONGPORT_ACCESS_TOKEN`，并视情况覆盖 `LONGPORT_API_BASE`、`LONGPORT_WS_URL`、`LONGPORT_TRADE_WS_URL`。
- 如需 Slack 告警，配置 `SLACK_WEBHOOK_URL`；`SignalDispatcher` 会在信号提交或冲突时推送摘要。
- watchlist 中列出的全部标的会一次性订阅 `SubType.Quote`，若需监听深度、分时等，可以扩展 `MarketDataService` 中的订阅逻辑。
- 订单经由 SDK 的 `TradeContext.submit_order` 发送，目前默认按照限价 Day 单提交（若未传价格则回退为市场单），可在 `src/longport_quant/execution/client.py` 中调整参数映射。
- `QuoteDataClient` (`src/longport_quant/data/quote_client.py`) 封装了常用的行情/资金/期权/轮证等查询接口，所有调用都通过 `asyncio.to_thread` 转换为异步，可在任务、脚本或策略中直接复用。
- `LongportTradingClient` 现已支持账户资金、仓位、订单/成交历史、最大可买数量等读写操作，便于风控和持仓同步。

## Development Workflow
- `scripts/run_strategy.py` boots the live trading loop.
- `scripts/ingest_watchlist.py` keeps the configured watchlist in sync with the database.
- `tests/` contains starting points for unit tests and stubs for integration pipelines.

## Roadmap
- Implement concrete strategy classes inheriting from `StrategyBase`.
- Flesh out PostgreSQL migrations and data validation.
- Add CI workflows for linting, unit tests, and backtesting.
# longport-quant-new
