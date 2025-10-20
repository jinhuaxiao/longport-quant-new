"""Helpers for syncing security universe and static fundamentals."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Iterable, Sequence

from loguru import logger
from longport import openapi
from sqlalchemy.dialects.postgresql import insert

from longport_quant.config.settings import Settings
from longport_quant.data.kline_sync import KlineDataService
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import SecurityUniverse


class SecurityUniverseSync:
    """Synchronise security universe lists and associated fundamentals."""

    def __init__(
        self,
        settings: Settings,
        db: DatabaseSessionManager,
        quote_client: QuoteDataClient,
    ) -> None:
        self._db = db
        self._quote_client = quote_client
        # Reuse existing K-line data service to persist static fundamentals.
        self._kline_service = KlineDataService(settings, db, quote_client)
        if settings.longport_api_base:
            os.environ.setdefault("LONGPORT_HTTP_URL", str(settings.longport_api_base))
        try:
            self._http_client = openapi.HttpClient.from_env()
        except Exception:  # pragma: no cover - optional fallback when env missing
            self._http_client = None

    async def sync_market(
        self,
        market: openapi.Market,
        *,
        include_static: bool = True,
        batch_size: int = 500,
        category: openapi.SecurityListCategory | str | None = None,
    ) -> dict[str, int]:
        """Fetch security list for a market and optionally sync static info."""

        logger.info("Fetching security list for {}", self._market_code(market))
        try:
            securities = await self._quote_client.list_securities(market, category)
        except openapi.OpenApiException as exc:  # pragma: no cover - network/remote error handling
            logger.error(
                "Failed to fetch security list for {} via QuoteContext: code={}, message={}",
                self._market_code(market),
                getattr(exc, "code", None),
                getattr(exc, "message", str(exc)),
            )
            if getattr(exc, "code", None) == 310010:
                securities = await self._list_securities_http(market, category)
            else:
                raise
        if not securities:
            logger.warning("Security list returned empty for {}", market)
            return {"universe": 0, "static": 0}

        await self._upsert_universe(securities, market)

        symbol_set = set()
        for sec in securities:
            symbol = getattr(sec, "symbol", None)
            if symbol is None and isinstance(sec, dict):
                symbol = sec.get("symbol")
            if symbol:
                symbol_set.add(symbol)
        symbols = sorted(symbol_set)

        if not include_static:
            return {"universe": len(symbols), "static": 0}

        total_static = 0
        for chunk in self._chunk(symbols, batch_size):
            total_static += await self._kline_service.sync_security_static(list(chunk))
        logger.info(
            "Synced static fundamentals for {} symbols in {}", total_static, self._market_code(market)
        )
        return {"universe": len(symbols), "static": total_static}

    async def _upsert_universe(
        self,
        securities: Sequence[object],
        market: openapi.Market,
    ) -> None:
        market_code = self._market_code(market)
        timestamp = datetime.utcnow()
        async with self._db.session() as session:
            for security in securities:
                symbol = getattr(security, "symbol", None)
                if symbol is None and isinstance(security, dict):
                    symbol = security.get("symbol")
                if not symbol:
                    continue
                row_market = self._infer_market(symbol, market_code)
                name_cn = getattr(security, "name_cn", None)
                name_en = getattr(security, "name_en", None)
                name_hk = getattr(security, "name_hk", None)
                if isinstance(security, dict):
                    name_cn = security.get("name_cn", name_cn)
                    name_en = security.get("name_en", name_en)
                    name_hk = security.get("name_hk", name_hk)
                stmt = insert(SecurityUniverse).values(
                    symbol=symbol,
                    market=row_market,
                    name_cn=name_cn,
                    name_en=name_en,
                    name_hk=name_hk,
                    updated_at=timestamp,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[SecurityUniverse.symbol],
                    set_={
                        "market": row_market,
                        "name_cn": name_cn,
                        "name_en": name_en,
                        "name_hk": name_hk,
                        "updated_at": timestamp,
                    },
                )
                await session.execute(stmt)
            await session.commit()
        logger.info(
            "Security universe for {} updated with {} entries",
            market_code,
            len(securities),
        )

    @staticmethod
    def _chunk(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
        for i in range(0, len(items), size):
            yield items[i : i + size]

    @staticmethod
    def _market_code(market: openapi.Market) -> str:
        return str(market).split(".")[-1].upper()

    @staticmethod
    def _infer_market(symbol: str, default: str) -> str:
        if "." in symbol:
            suffix = symbol.split(".")[-1]
            if suffix:
                return suffix.upper()
        return default

    async def _list_securities_http(
        self,
        market: openapi.Market,
        category: openapi.SecurityListCategory | str | None,
    ) -> list[dict[str, str]]:
        if not self._http_client:
            logger.error(
                "HTTP client not initialised; cannot fallback to REST security list"
            )
            return []

        payload = {"market": self._market_code(market)}
        category_label = self._category_label(category)
        if category_label:
            payload["category"] = category_label
        try:
            response = await asyncio.to_thread(
                self._http_client.request,
                "post",
                "/v1/quote/get_security_list",
                body=payload,
            )
        except openapi.OpenApiException as exc:  # pragma: no cover - network
            logger.error(
                "HTTP security list failed for {}: code={}, message={}",
                payload["market"],
                getattr(exc, "code", None),
                getattr(exc, "message", str(exc)),
            )
            return []

        data = None
        if isinstance(response, dict):
            data = response.get("data") or response.get("items")
        if not isinstance(data, list):
            logger.warning(
                "Unexpected security list payload for {}: {}",
                payload["market"],
                response,
            )
            return []

        records: list[dict[str, str]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            symbol = item.get("symbol")
            if not symbol:
                continue
            records.append(
                {
                    "symbol": symbol,
                    "name_cn": item.get("name_cn"),
                    "name_en": item.get("name_en"),
                    "name_hk": item.get("name_hk"),
                }
            )
        logger.info(
            "Fetched {} securities for {} via HTTP fallback",
            len(records),
            payload["market"],
        )
        return records

    @staticmethod
    def _category_label(category: openapi.SecurityListCategory | str | None) -> str | None:
        if category is None:
            return None
        if isinstance(category, openapi.SecurityListCategory):
            return getattr(category, "__name__", None) or str(category).split(".")[-1]
        return str(category)


__all__ = ["SecurityUniverseSync"]
