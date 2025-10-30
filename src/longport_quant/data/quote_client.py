"""Convenience wrappers around Longport QuoteContext for data retrieval."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Iterable, List, Optional

from loguru import logger
from longport import OpenApiException, openapi

from longport_quant.config.sdk import build_sdk_config
from longport_quant.config.settings import Settings


class QuoteDataClient:
    """Thread-safe asynchronous facade for quote-related SDK methods."""

    def __init__(self, settings: Settings, config: openapi.Config | None = None) -> None:
        self._settings = settings
        self._config = config
        self._ctx: openapi.QuoteContext | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "QuoteDataClient":
        await self._ensure_context()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """清理资源，关闭连接"""
        if self._ctx is not None:
            try:
                # 尝试取消所有订阅（如果有的话）
                try:
                    subs = await asyncio.to_thread(self._ctx.subscriptions)
                    if subs:
                        logger.debug(f"Unsubscribing {len(subs)} subscriptions before closing QuoteContext")
                        for sub in subs:
                            try:
                                await asyncio.to_thread(
                                    self._ctx.unsubscribe,
                                    [sub.symbol],
                                    [sub.sub_types[0]] if sub.sub_types else []
                                )
                            except Exception as e:
                                logger.debug(f"Failed to unsubscribe {sub.symbol}: {e}")
                except Exception as e:
                    logger.debug(f"Failed to get subscriptions: {e}")

                # 强制删除对象，触发底层资源清理
                ctx_to_delete = self._ctx
                self._ctx = None
                del ctx_to_delete

                # 建议垃圾回收器立即回收
                import gc
                gc.collect()

                logger.debug("QuoteContext cleaned up and resources released")
            except Exception as e:
                logger.warning(f"Error during QuoteContext cleanup: {e}")
                self._ctx = None

    async def get_static_info(self, symbols: List[str]) -> List[openapi.SecurityStaticInfo]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.static_info, symbols)

    async def get_realtime_quote(self, symbols: List[str]) -> List[openapi.SecurityQuote]:
        """
        获取实时行情

        注意: realtime_quote方法在某些情况下返回空列表
        因此改用quote方法作为备选
        """
        ctx = await self._ensure_context()

        # 尝试使用realtime_quote
        try:
            quotes = await asyncio.to_thread(ctx.realtime_quote, symbols)
            if quotes:
                return quotes
        except Exception as e:
            logger.debug(f"realtime_quote失败，尝试使用quote: {e}")

        # 备选方案：使用quote方法
        try:
            quotes = await asyncio.to_thread(ctx.quote, symbols)
            return quotes if quotes else []
        except Exception as e:
            logger.error(f"quote方法也失败: {e}")
            return []

    async def get_option_quote(self, symbols: List[str]) -> List[openapi.OptionQuote]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.option_quote, symbols)

    async def get_warrant_quote(self, symbols: List[str]) -> List[openapi.WarrantQuote]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.warrant_quote, symbols)

    async def get_depth(self, symbol: str) -> openapi.SecurityDepth:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.realtime_depth, symbol)

    async def get_brokers(self, symbol: str) -> openapi.SecurityBrokers:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.realtime_brokers, symbol)

    async def get_participants(self) -> openapi.ParticipantInfo:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.participants)

    async def get_trades(self, symbol: str, count: int = 100) -> List[openapi.Trade]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.realtime_trades, symbol, count)

    async def get_intraday(self, symbol: str) -> openapi.IntradayLine:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.intraday, symbol)

    async def get_candlesticks(
        self,
        symbol: str,
        period: openapi.Period,
        count: int,
        adjust_type: openapi.AdjustType,
    ) -> List[openapi.Candlestick]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(
            ctx.candlesticks,
            symbol,
            period,
            count,
            adjust_type,
        )

    async def get_history_candles(
        self,
        symbol: str,
        period: openapi.Period,
        adjust_type: openapi.AdjustType,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[openapi.Candlestick]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(
            ctx.history_candlesticks_by_date,
            symbol,
            period,
            adjust_type,
            start,
            end,
        )

    async def get_history_candles_by_offset(
        self,
        symbol: str,
        period: openapi.Period,
        adjust_type: openapi.AdjustType,
        forward: bool,
        count: int,
    ) -> List[openapi.Candlestick]:
        """
        获取历史K线数据（通过偏移量）

        Args:
            symbol: 标的代码
            period: 周期
            adjust_type: 复权类型
            forward: 是否向前查询（True=向前，False=向后查询历史数据）
            count: K线数量

        Returns:
            K线列表
        """
        ctx = await self._ensure_context()
        return await asyncio.to_thread(
            ctx.history_candlesticks_by_offset,
            symbol,
            period,
            adjust_type,
            forward,
            count,
        )

    async def get_option_expirations(self, symbol: str) -> List[date]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.option_chain_expiry_date_list, symbol)

    async def get_option_chain(self, symbol: str, expiry_date: date) -> List[openapi.OptionQuote]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.option_chain_info_by_date, symbol, expiry_date)

    async def get_warrant_issuers(self) -> openapi.IssuerInfo:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.warrant_issuers)

    async def filter_warrants(
        self,
        symbol: str,
        sort_by: openapi.WarrantSortBy,
        sort_order: openapi.SortOrderType,
        **filters,
    ) -> openapi.WarrantInfo:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.warrant_list, symbol, sort_by, sort_order, **filters)

    async def get_trading_session(self) -> openapi.TradingSessionInfo:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.trading_session)

    async def get_trading_days(
        self,
        market: openapi.Market,
        begin: date,
        end: date,
    ) -> openapi.MarketTradingDays:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.trading_days, market, begin, end)

    async def get_capital_flow(self, symbol: str) -> openapi.CapitalFlowLine:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.capital_flow, symbol)

    async def get_capital_distribution(self, symbol: str) -> openapi.CapitalDistributionResponse:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.capital_distribution, symbol)

    async def get_calc_index(self, symbols: List[str], indexes: List[openapi.CalcIndex]) -> List[openapi.SecurityCalcIndex]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.calc_indexes, symbols, indexes)

    async def get_market_temperature(self, market: openapi.Market) -> openapi.MarketTemperature:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.market_temperature, market)

    async def get_history_market_temperature(
        self,
        market: openapi.Market,
        start_date: date,
        end_date: date,
    ) -> openapi.HistoryMarketTemperatureResponse:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.history_market_temperature, market, start_date, end_date)

    async def list_securities(
        self,
        market: openapi.Market | str,
        category: openapi.SecurityListCategory | str | None = None,
    ) -> List[openapi.Security]:
        ctx = await self._ensure_context()

        market_param = self._normalise_market(market)
        category_param = self._normalise_security_list_category(category)

        return await asyncio.to_thread(ctx.security_list, market_param, category_param)

    @staticmethod
    def _normalise_market(market: openapi.Market | str) -> openapi.Market:
        if isinstance(market, openapi.Market):
            return market
        upper = market.upper()
        mapping = {
            "HK": openapi.Market.HK,
            "US": openapi.Market.US,
            "CN": openapi.Market.CN,
            "SG": openapi.Market.SG,
            "CRYPTO": openapi.Market.Crypto,
        }
        if upper not in mapping:
            raise ValueError(f"Unsupported market code: {market}")
        return mapping[upper]

    @staticmethod
    def _normalise_security_list_category(
        category: openapi.SecurityListCategory | str | None,
    ) -> openapi.SecurityListCategory | None:
        if category is None:
            return None
        if isinstance(category, openapi.SecurityListCategory):
            return category
        upper = category.upper()
        mapping = {
            "OVERNIGHT": openapi.SecurityListCategory.Overnight,
        }
        if upper not in mapping:
            raise ValueError(f"Unsupported security list category: {category}")
        return mapping[upper]

    async def create_watchlist_group(
        self,
        name: str,
        securities: Iterable[openapi.WatchlistSecurity] | None = None,
    ) -> openapi.WatchlistGroup:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.create_watchlist_group, name, securities)

    async def delete_watchlist_group(self, group_id: int, purge: bool = False) -> None:
        ctx = await self._ensure_context()
        await asyncio.to_thread(ctx.delete_watchlist_group, group_id, purge)

    async def list_watchlist_groups(self) -> List[openapi.WatchlistGroup]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.watchlist)

    async def update_watchlist_group(
        self,
        group_id: int,
        name: Optional[str] = None,
        securities: Iterable[openapi.WatchlistSecurity] | None = None,
        mode: openapi.SecuritiesUpdateMode | None = None,
    ) -> openapi.WatchlistGroup:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(
            ctx.update_watchlist_group,
            group_id,
            name,
            securities,
            mode,
        )

    async def subscribe(
        self,
        symbols: List[str],
        sub_types: List[openapi.SubType],
        is_first_push: bool = False,
    ) -> None:
        ctx = await self._ensure_context()
        await asyncio.to_thread(ctx.subscribe, symbols, sub_types, is_first_push)

    async def unsubscribe(self, symbols: List[str], sub_types: List[openapi.SubType]) -> None:
        ctx = await self._ensure_context()
        await asyncio.to_thread(ctx.unsubscribe, symbols, sub_types)

    async def subscriptions(self) -> List[openapi.Subscription]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.subscriptions)

    async def set_on_quote(self, callback):
        ctx = await self._ensure_context()
        await asyncio.to_thread(ctx.set_on_quote, callback)

    async def set_on_depth(self, callback):
        ctx = await self._ensure_context()
        await asyncio.to_thread(ctx.set_on_depth, callback)

    async def set_on_brokers(self, callback):
        ctx = await self._ensure_context()
        await asyncio.to_thread(ctx.set_on_brokers, callback)

    async def set_on_trades(self, callback):
        ctx = await self._ensure_context()
        await asyncio.to_thread(ctx.set_on_trades, callback)

    async def set_on_candlestick(self, callback):
        ctx = await self._ensure_context()
        await asyncio.to_thread(ctx.set_on_candlestick, callback)

    async def subscribe_candlesticks(
        self,
        symbol: str,
        period: openapi.Period,
    ) -> None:
        ctx = await self._ensure_context()
        await asyncio.to_thread(ctx.subscribe_candlesticks, symbol, period)

    async def unsubscribe_candlesticks(
        self,
        symbol: str,
        period: openapi.Period,
    ) -> None:
        ctx = await self._ensure_context()
        await asyncio.to_thread(ctx.unsubscribe_candlesticks, symbol, period)

    async def get_quote_level(self) -> str:
        ctx = await self._ensure_context()
        level = await asyncio.to_thread(lambda: ctx.quote_level)
        return str(level)

    async def get_quote_package_details(self) -> List[dict]:
        ctx = await self._ensure_context()
        details = await asyncio.to_thread(ctx.quote_package_details)
        # Convert to dict list for easier handling
        return [{"name": d.name, "description": d.description} for d in details] if details else []

    async def _ensure_context(self) -> openapi.QuoteContext:
        async with self._lock:
            if self._ctx is None:
                config = self._config or build_sdk_config(self._settings)
                self._config = config
                logger.debug("Initialising QuoteContext for data client")
                self._ctx = await asyncio.to_thread(openapi.QuoteContext, config)
        return self._ctx


__all__ = ["QuoteDataClient"]
