"""Enhanced market data service with multi-type support and reliability features."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from functools import partial
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from loguru import logger
from longport import OpenApiException, openapi

from longport_quant.config.sdk import build_sdk_config
from longport_quant.config.settings import Settings, get_settings
from longport_quant.data.watchlist import Watchlist, WatchlistLoader
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import (
    MarketDepth,
    RealtimeQuote,
    TradeTick,
)
from longport_quant.utils.events import EventBus
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert


class DataType(Enum):
    """Supported market data types."""

    QUOTE = "quote"
    DEPTH = "depth"
    TRADE = "trade"
    BROKER = "broker"


@dataclass
class MarketDataConfig:
    """Configuration for market data subscription."""

    enable_quote: bool = True
    enable_depth: bool = True
    enable_trade: bool = True
    enable_broker: bool = False
    persist_to_db: bool = True
    queue_size: int = 10000
    batch_size: int = 100
    flush_interval: float = 1.0  # seconds
    reconnect_delay: float = 5.0  # seconds
    max_reconnect_attempts: int = 10


@dataclass
class ConnectionStatus:
    """Connection status tracking."""

    connected: bool = False
    last_connected: Optional[datetime] = None
    last_disconnected: Optional[datetime] = None
    reconnect_attempts: int = 0
    total_messages: int = 0
    messages_by_type: Dict[str, int] = field(default_factory=dict)


class PersistenceQueue:
    """Queue for persisting market data to database."""

    def __init__(
        self,
        db: DatabaseSessionManager,
        batch_size: int = 100,
        flush_interval: float = 1.0,
    ):
        self._db = db
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._queues: Dict[str, deque] = {
            DataType.QUOTE.value: deque(maxlen=10000),
            DataType.DEPTH.value: deque(maxlen=10000),
            DataType.TRADE.value: deque(maxlen=10000),
        }
        self._running = False
        self._flush_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the persistence queue."""
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info("Persistence queue started")

    async def stop(self):
        """Stop the persistence queue."""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        # Flush remaining data
        await self._flush_all()
        logger.info("Persistence queue stopped")

    async def enqueue(self, data_type: DataType, data: Dict[str, Any]):
        """Add data to the queue."""
        if data_type.value in self._queues:
            self._queues[data_type.value].append(data)

    async def _flush_loop(self):
        """Periodically flush queues to database."""
        while self._running:
            try:
                await asyncio.sleep(self._flush_interval)
                await self._flush_all()
            except Exception as e:
                logger.error(f"Error in flush loop: {e}")

    async def _flush_all(self):
        """Flush all queues to database."""
        for data_type in DataType:
            if data_type.value in self._queues:
                await self._flush_queue(data_type)

    async def _flush_queue(self, data_type: DataType):
        """Flush a specific queue to database."""
        queue = self._queues[data_type.value]
        if not queue:
            return

        batch = []
        while queue and len(batch) < self._batch_size:
            batch.append(queue.popleft())

        if not batch:
            return

        try:
            async with self._db.session() as session:
                if data_type == DataType.QUOTE:
                    await self._persist_quotes(session, batch)
                elif data_type == DataType.DEPTH:
                    await self._persist_depths(session, batch)
                elif data_type == DataType.TRADE:
                    await self._persist_trades(session, batch)

                await session.commit()
                logger.debug(f"Flushed {len(batch)} {data_type.value} records")

        except Exception as e:
            logger.error(f"Error persisting {data_type.value} data: {e}")
            # Re-queue failed items
            for item in reversed(batch):
                self._queues[data_type.value].appendleft(item)

    async def _persist_quotes(self, session, quotes: List[Dict]):
        """Persist quote data."""
        for quote in quotes:
            timestamp = quote.get("timestamp", datetime.now())
            stmt = insert(RealtimeQuote).values(
                symbol=quote["symbol"],
                timestamp=timestamp,
                last_done=self._to_decimal(quote.get("last_price")),
                prev_close=self._to_decimal(quote.get("prev_close")),
                open=self._to_decimal(quote.get("open")),
                high=self._to_decimal(quote.get("high")),
                low=self._to_decimal(quote.get("low")),
                volume=quote.get("volume"),
                turnover=self._to_decimal(quote.get("turnover")),
                bid_price=self._to_decimal(quote.get("bid_price")),
                ask_price=self._to_decimal(quote.get("ask_price")),
                bid_volume=quote.get("bid_size"),
                ask_volume=quote.get("ask_size"),
                trade_status=quote.get("trade_status"),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[RealtimeQuote.symbol, RealtimeQuote.timestamp],
                set_={
                    "last_done": stmt.excluded.last_done,
                    "prev_close": stmt.excluded.prev_close,
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "volume": stmt.excluded.volume,
                    "turnover": stmt.excluded.turnover,
                    "bid_price": stmt.excluded.bid_price,
                    "ask_price": stmt.excluded.ask_price,
                    "bid_volume": stmt.excluded.bid_volume,
                    "ask_volume": stmt.excluded.ask_volume,
                    "trade_status": stmt.excluded.trade_status,
                },
            )
            await session.execute(stmt)

    async def _persist_depths(self, session, depths: List[Dict]):
        """Persist market depth data."""
        for depth in depths:
            timestamp = depth.get("timestamp", datetime.now())

            bids = self._zip_depth_levels(depth.get("bid_prices", []), depth.get("bid_sizes", []))
            asks = self._zip_depth_levels(depth.get("ask_prices", []), depth.get("ask_sizes", []))

            for position, price, size in bids:
                stmt = insert(MarketDepth).values(
                    symbol=depth["symbol"],
                    timestamp=timestamp,
                    position=position,
                    side="BID",
                    price=self._to_decimal(price),
                    volume=size,
                    broker_count=None,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[
                        MarketDepth.symbol,
                        MarketDepth.timestamp,
                        MarketDepth.position,
                        MarketDepth.side,
                    ],
                    set_={
                        "price": stmt.excluded.price,
                        "volume": stmt.excluded.volume,
                        "broker_count": stmt.excluded.broker_count,
                    },
                )
                await session.execute(stmt)

            for position, price, size in asks:
                stmt = insert(MarketDepth).values(
                    symbol=depth["symbol"],
                    timestamp=timestamp,
                    position=position,
                    side="ASK",
                    price=self._to_decimal(price),
                    volume=size,
                    broker_count=None,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[
                        MarketDepth.symbol,
                        MarketDepth.timestamp,
                        MarketDepth.position,
                        MarketDepth.side,
                    ],
                    set_={
                        "price": stmt.excluded.price,
                        "volume": stmt.excluded.volume,
                        "broker_count": stmt.excluded.broker_count,
                    },
                )
                await session.execute(stmt)

    async def _persist_trades(self, session, trades: List[Dict]):
        """Persist trade tick data."""
        for trade in trades:
            stmt = insert(TradeTick).values(
                symbol=trade["symbol"],
                price=self._to_decimal(trade.get("price")),
                volume=trade.get("volume"),
                timestamp=trade.get("timestamp", datetime.now()),
                direction=trade.get("direction", "neutral"),
                trade_type=trade.get("trade_type", "auto"),
            )
            await session.execute(stmt)

    @staticmethod
    def _to_decimal(value: Any) -> Optional[Decimal]:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    @staticmethod
    def _zip_depth_levels(prices: List[Any], sizes: List[Any]) -> List[Tuple[int, float, int]]:
        levels = []
        for idx, (price, size) in enumerate(zip(prices, sizes), start=1):
            if price is None or size is None:
                continue
            try:
                price_val = float(price)
                size_val = int(size)
            except (TypeError, ValueError):
                continue
            levels.append((idx, price_val, size_val))
        return levels


class EnhancedMarketDataService:
    """Enhanced market data service with multiple data types and reliability."""

    def __init__(
        self,
        settings: Settings,
        db: DatabaseSessionManager,
        config: MarketDataConfig | None = None,
    ):
        self._settings = settings
        self._db = db
        self._config = config or MarketDataConfig()
        self._sdk_config: Optional[openapi.Config] = None
        self._quote_ctx: Optional[openapi.QuoteContext] = None
        self._event_bus = EventBus()
        self._watchlist_loader = WatchlistLoader()
        self._watchlist: Optional[Watchlist] = None
        self._persistence_queue: Optional[PersistenceQueue] = None
        self._status = ConnectionStatus()
        self._running = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._subscribed_types: Set[openapi.SubType] = set()

    async def __aenter__(self) -> "EnhancedMarketDataService":
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> Optional[bool]:
        """Async context manager exit."""
        await self.stop()
        return None

    async def start(self):
        """Start the market data service."""
        logger.info("Starting enhanced market data service")
        self._running = True

        # Load watchlist
        self._watchlist = self._watchlist_loader.load()
        if not self._watchlist.items:
            logger.warning("Watchlist is empty; no data will be streamed")
            return

        # Initialize persistence queue
        if self._config.persist_to_db:
            self._persistence_queue = PersistenceQueue(
                self._db,
                batch_size=self._config.batch_size,
                flush_interval=self._config.flush_interval,
            )
            await self._persistence_queue.start()

        # Connect to market data
        await self._connect()

        # Start reconnection monitor
        self._reconnect_task = asyncio.create_task(self._reconnect_monitor())

    async def stop(self):
        """Stop the market data service."""
        logger.info("Stopping enhanced market data service")
        self._running = False

        # Cancel reconnection task
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        # Disconnect from market data
        await self._disconnect()

        # Stop persistence queue
        if self._persistence_queue:
            await self._persistence_queue.stop()

        self._status.connected = False
        logger.info("Market data service stopped")

    async def _connect(self) -> bool:
        """Connect to market data feed."""
        try:
            # Build SDK config
            self._sdk_config = build_sdk_config(self._settings)

            # Create quote context
            self._quote_ctx = await asyncio.to_thread(
                openapi.QuoteContext, self._sdk_config
            )

            # Set up callbacks
            self._setup_callbacks()

            # Subscribe to data types
            await self._subscribe_all()

            self._status.connected = True
            self._status.last_connected = datetime.now()
            self._status.reconnect_attempts = 0

            logger.info("Successfully connected to market data feed")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to market data: {e}")
            self._status.connected = False
            self._status.last_disconnected = datetime.now()
            return False

    async def _disconnect(self):
        """Disconnect from market data feed."""
        if self._quote_ctx and self._watchlist:
            try:
                # Unsubscribe all data types
                await self._unsubscribe_all()

                # Close context
                self._quote_ctx = None

                self._status.connected = False
                self._status.last_disconnected = datetime.now()

                logger.info("Disconnected from market data feed")

            except Exception as e:
                logger.error(f"Error during disconnect: {e}")

    async def _reconnect_monitor(self):
        """Monitor connection and reconnect if necessary."""
        while self._running:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds

                if not self._status.connected and self._watchlist and self._watchlist.items:
                    self._status.reconnect_attempts += 1

                    if self._status.reconnect_attempts <= self._config.max_reconnect_attempts:
                        logger.info(
                            f"Attempting reconnection ({self._status.reconnect_attempts}/"
                            f"{self._config.max_reconnect_attempts})"
                        )

                        # Wait before reconnecting
                        await asyncio.sleep(self._config.reconnect_delay)

                        # Attempt reconnection
                        if await self._connect():
                            logger.info("Reconnection successful")
                        else:
                            logger.warning("Reconnection failed")
                    else:
                        logger.error("Max reconnection attempts reached")
                        self._running = False

            except Exception as e:
                logger.error(f"Error in reconnect monitor: {e}")

    def _setup_callbacks(self):
        """Set up SDK callbacks for different data types."""
        if not self._quote_ctx:
            return

        # Quote callback
        if self._config.enable_quote:
            self._quote_ctx.set_on_quote(self._handle_quote)  # type: ignore

        # Depth callback
        if self._config.enable_depth:
            self._quote_ctx.set_on_depth(self._handle_depth)  # type: ignore

        # Trade callback
        if self._config.enable_trade:
            self._quote_ctx.set_on_trades(self._handle_trades)  # type: ignore

        # Broker callback
        if self._config.enable_broker:
            self._quote_ctx.set_on_brokers(self._handle_brokers)  # type: ignore

    async def _subscribe_all(self):
        """Subscribe to all configured data types."""
        if not self._quote_ctx or not self._watchlist:
            return

        symbols = self._watchlist.symbols()
        sub_types = []

        if self._config.enable_quote:
            sub_types.append(openapi.SubType.Quote)
        if self._config.enable_depth:
            sub_types.append(openapi.SubType.Depth)
        if self._config.enable_trade:
            sub_types.append(openapi.SubType.Trade)
        if self._config.enable_broker:
            sub_types.append(openapi.SubType.Brokers)

        if not sub_types:
            logger.warning("No data types enabled for subscription")
            return

        try:
            logger.info(
                f"Subscribing to {len(symbols)} symbols for {len(sub_types)} data types"
            )
            await asyncio.to_thread(
                self._quote_ctx.subscribe, symbols, sub_types, True
            )
            self._subscribed_types = set(sub_types)

        except OpenApiException as e:
            logger.error(f"Subscription failed: {e}")
            raise

    async def _unsubscribe_all(self):
        """Unsubscribe from all data types."""
        if not self._quote_ctx or not self._watchlist or not self._subscribed_types:
            return

        try:
            symbols = self._watchlist.symbols()
            await asyncio.to_thread(
                self._quote_ctx.unsubscribe,
                symbols,
                list(self._subscribed_types),
            )
            self._subscribed_types.clear()

        except OpenApiException as e:
            logger.warning(f"Unsubscribe failed: {e}")

    def _handle_quote(self, symbol: str, event: openapi.PushQuote) -> None:
        """Handle quote push event."""
        if not self._running:
            return

        try:
            # Update statistics
            self._status.total_messages += 1
            self._status.messages_by_type["quote"] = (
                self._status.messages_by_type.get("quote", 0) + 1
            )

            # Extract quote data
            quote_data = {
                "symbol": symbol,
                "last_price": float(event.last_done) if hasattr(event, "last_done") and event.last_done else None,
                "prev_close": float(event.previous_close) if hasattr(event, "previous_close") and event.previous_close else None,
                "open": float(event.open) if hasattr(event, "open") and event.open else None,
                "high": float(event.high) if hasattr(event, "high") and event.high else None,
                "low": float(event.low) if hasattr(event, "low") and event.low else None,
                "volume": int(event.volume) if hasattr(event, "volume") and event.volume else None,
                "turnover": float(event.turnover) if hasattr(event, "turnover") and event.turnover else None,
                "bid_price": float(event.bid_price) if hasattr(event, "bid_price") and event.bid_price else None,
                "bid_size": int(event.bid_size) if hasattr(event, "bid_size") and event.bid_size else None,
                "ask_price": float(event.ask_price) if hasattr(event, "ask_price") and event.ask_price else None,
                "ask_size": int(event.ask_size) if hasattr(event, "ask_size") and event.ask_size else None,
                "trade_status": getattr(event, "trade_status", None),
                "timestamp": datetime.fromtimestamp(event.timestamp / 1000) if hasattr(event, "timestamp") and event.timestamp else datetime.now(),
            }

            # Publish event
            asyncio.create_task(
                self._publish_and_persist(DataType.QUOTE, quote_data)
            )

        except Exception as e:
            logger.error(f"Error handling quote for {symbol}: {e}")

    def _handle_depth(self, symbol: str, event: openapi.PushDepth) -> None:
        """Handle market depth push event."""
        if not self._running:
            return

        try:
            # Update statistics
            self._status.total_messages += 1
            self._status.messages_by_type["depth"] = (
                self._status.messages_by_type.get("depth", 0) + 1
            )

            # Extract depth data
            bid_prices = []
            bid_sizes = []
            ask_prices = []
            ask_sizes = []

            if hasattr(event, "bids") and event.bids:
                bid_prices = [float(level.price) if hasattr(level, "price") else float(level) for level in event.bids]
            if not bid_prices and hasattr(event, "bid_prices") and event.bid_prices:
                bid_prices = [float(price) for price in event.bid_prices]

            if hasattr(event, "bid_sizes") and event.bid_sizes:
                bid_sizes = [int(level) for level in event.bid_sizes]
            if not bid_sizes and hasattr(event, "bid_volumes") and event.bid_volumes:
                bid_sizes = [int(volume) for volume in event.bid_volumes]

            if hasattr(event, "asks") and event.asks:
                ask_prices = [float(level.price) if hasattr(level, "price") else float(level) for level in event.asks]
            if not ask_prices and hasattr(event, "ask_prices") and event.ask_prices:
                ask_prices = [float(price) for price in event.ask_prices]

            if hasattr(event, "ask_sizes") and event.ask_sizes:
                ask_sizes = [int(level) for level in event.ask_sizes]
            if not ask_sizes and hasattr(event, "ask_volumes") and event.ask_volumes:
                ask_sizes = [int(volume) for volume in event.ask_volumes]

            depth_data = {
                "symbol": symbol,
                "bid_prices": bid_prices,
                "bid_sizes": bid_sizes,
                "ask_prices": ask_prices,
                "ask_sizes": ask_sizes,
                "timestamp": datetime.now(),
            }

            # Publish event
            asyncio.create_task(
                self._publish_and_persist(DataType.DEPTH, depth_data)
            )

        except Exception as e:
            logger.error(f"Error handling depth for {symbol}: {e}")

    def _handle_trades(self, symbol: str, trades: List[openapi.PushTrade]) -> None:
        """Handle trade push events."""
        if not self._running:
            return

        try:
            for trade in trades:
                # Update statistics
                self._status.total_messages += 1
                self._status.messages_by_type["trade"] = (
                    self._status.messages_by_type.get("trade", 0) + 1
                )

                # Extract trade data
                trade_type = getattr(trade, "trade_type", "auto")
                direction = str(trade_type) if trade_type is not None else "neutral"

                trade_data = {
                    "symbol": symbol,
                    "price": float(trade.price) if hasattr(trade, "price") and trade.price else None,
                    "volume": int(trade.volume) if hasattr(trade, "volume") and trade.volume else None,
                    "timestamp": datetime.fromtimestamp(trade.timestamp / 1000) if hasattr(trade, "timestamp") and trade.timestamp else datetime.now(),
                    "direction": direction,
                    "trade_type": direction,
                }

                # Publish event
                asyncio.create_task(
                    self._publish_and_persist(DataType.TRADE, trade_data)
                )

        except Exception as e:
            logger.error(f"Error handling trades for {symbol}: {e}")

    def _handle_brokers(self, symbol: str, event: openapi.PushBrokers) -> None:
        """Handle broker push event."""
        if not self._running:
            return

        try:
            # Update statistics
            self._status.total_messages += 1
            self._status.messages_by_type["broker"] = (
                self._status.messages_by_type.get("broker", 0) + 1
            )

            # Extract broker data
            broker_data = {
                "symbol": symbol,
                "ask_brokers": event.ask_brokers if hasattr(event, "ask_brokers") else [],
                "bid_brokers": event.bid_brokers if hasattr(event, "bid_brokers") else [],
                "timestamp": datetime.now(),
            }

            # Publish event (no persistence for broker data)
            asyncio.create_task(
                self._event_bus.publish(DataType.BROKER.value, broker_data)
            )

        except Exception as e:
            logger.error(f"Error handling brokers for {symbol}: {e}")

    async def _publish_and_persist(self, data_type: DataType, data: Dict[str, Any]):
        """Publish event and persist to database."""
        # Publish to event bus
        await self._event_bus.publish(data_type.value, data)

        # Add to persistence queue
        if self._config.persist_to_db and self._persistence_queue:
            await self._persistence_queue.enqueue(data_type, data)

    def subscribe(
        self,
        data_type_or_handler: DataType | Callable[[Dict], Awaitable[None]],
        handler: Optional[Callable[[Dict], Awaitable[None]]] = None,
    ) -> None:
        """Subscribe to a specific data type (defaults to quotes)."""

        if handler is None:
            data_type = DataType.QUOTE
            callback = data_type_or_handler  # type: ignore[assignment]
        else:
            data_type = data_type_or_handler  # type: ignore[assignment]
            callback = handler

        if not callable(callback):
            raise TypeError("Handler must be callable")

        self._event_bus.subscribe(data_type.value, callback)  # type: ignore[arg-type]

    def unsubscribe(
        self,
        data_type_or_handler: DataType | Callable[[Dict], Awaitable[None]],
        handler: Optional[Callable[[Dict], Awaitable[None]]] = None,
    ) -> None:
        """Unsubscribe from a specific data type (defaults to quotes)."""

        if handler is None:
            data_type = DataType.QUOTE
            callback = data_type_or_handler  # type: ignore[assignment]
        else:
            data_type = data_type_or_handler  # type: ignore[assignment]
            callback = handler

        if not callable(callback):
            raise TypeError("Handler must be callable")

        self._event_bus.unsubscribe(data_type.value, callback)  # type: ignore[arg-type]

    def get_status(self) -> ConnectionStatus:
        """Get current connection status."""
        return self._status

    async def add_symbols(self, symbols: List[str]):
        """Add symbols to subscription."""
        if not self._quote_ctx or not symbols:
            return

        try:
            sub_types = list(self._subscribed_types)
            if sub_types:
                await asyncio.to_thread(
                    self._quote_ctx.subscribe, symbols, sub_types, False
                )
                logger.info(f"Added {len(symbols)} symbols to subscription")

        except OpenApiException as e:
            logger.error(f"Failed to add symbols: {e}")

    async def remove_symbols(self, symbols: List[str]):
        """Remove symbols from subscription."""
        if not self._quote_ctx or not symbols:
            return

        try:
            sub_types = list(self._subscribed_types)
            if sub_types:
                await asyncio.to_thread(
                    self._quote_ctx.unsubscribe, symbols, sub_types
                )
                logger.info(f"Removed {len(symbols)} symbols from subscription")

        except OpenApiException as e:
            logger.error(f"Failed to remove symbols: {e}")


__all__ = [
    "EnhancedMarketDataService",
    "MarketDataConfig",
    "DataType",
    "ConnectionStatus",
    "PersistenceQueue",
]
