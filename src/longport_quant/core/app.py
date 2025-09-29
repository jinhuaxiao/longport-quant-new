"""Main async application bootstrap."""

import asyncio
import signal
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncIterator

from loguru import logger

from longport_quant.config import get_settings
from longport_quant.config.sdk import build_sdk_config
from longport_quant.core.logging import configure_logging
from longport_quant.data.market_data_service import MarketDataService
from longport_quant.notifications import SlackNotifier
from longport_quant.execution.order_router import OrderRouter
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.portfolio.state import PortfolioService
from longport_quant.risk.checks import RiskEngine
from longport_quant.strategy.manager import StrategyManager


@asynccontextmanager
async def application_lifespan() -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    logger.info("Booting Longport Quant in {} mode", settings.environment)

    async with AsyncExitStack() as stack:
        db_manager = DatabaseSessionManager(settings.database_dsn)
        await stack.enter_async_context(db_manager)

        sdk_config = build_sdk_config(settings)
        slack = SlackNotifier(settings.slack_webhook_url)

        market_data = MarketDataService(settings, sdk_config)
        await stack.enter_async_context(market_data)

        order_router = OrderRouter(settings, sdk_config)
        await stack.enter_async_context(order_router)

        await stack.enter_async_context(slack)

        portfolio = PortfolioService(db_manager)
        risk_engine = RiskEngine(settings, portfolio)
        order_router.bind_risk_engine(risk_engine)
        strategies = StrategyManager(
            settings,
            market_data,
            order_router,
            risk_engine,
            portfolio,
            slack,
        )
        await stack.enter_async_context(strategies)

        yield

    logger.info("Shutdown complete")


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(_graceful_exit(s)))
        except NotImplementedError:  # pragma: no cover
            logger.warning("Signal handlers not supported on this platform")


async def _graceful_exit(sig: signal.Signals) -> None:
    logger.info("Received {}, shutting down gracefully", sig.name)
    tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def run() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _install_signal_handlers(loop)

    async def _main() -> None:
        async with application_lifespan():
            stop_event = asyncio.Event()
            await stop_event.wait()

    try:
        loop.run_until_complete(_main())
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    run()
