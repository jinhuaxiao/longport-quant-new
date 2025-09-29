"""Watchlist management for curated tickers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from longport_quant.config import get_settings


@dataclass(frozen=True)
class WatchItem:
    symbol: str
    market: str


@dataclass
class Watchlist:
    items: list[WatchItem]

    def symbols(self, market: str | None = None) -> list[str]:
        if market:
            return [item.symbol for item in self.items if item.market.lower() == market.lower()]
        return [item.symbol for item in self.items]

    def __iter__(self) -> Iterable[WatchItem]:
        return iter(self.items)


class WatchlistLoader:
    """Load watchlist definitions from YAML."""

    def __init__(self, path: Path | None = None) -> None:
        settings = get_settings()
        self._settings = settings
        self.path = path or settings.watchlist_path

    def load(self) -> Watchlist:
        if not self.path.exists():
            raise FileNotFoundError(f"Watchlist file not found: {self.path}")

        with self.path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}

        items: list[WatchItem] = []
        seen: set[tuple[str, str]] = set()

        for market, symbols in payload.get("markets", {}).items():
            for raw in symbols or []:
                norm_symbol = self._normalize_symbol(str(raw), market)
                key = (norm_symbol, market.lower())
                if key in seen:
                    continue
                seen.add(key)
                items.append(WatchItem(symbol=norm_symbol, market=market))

        if missing := payload.get("symbols"):
            for entry in missing:
                if isinstance(entry, dict):
                    market = str(entry["market"]).lower()
                    symbol = self._normalize_symbol(str(entry["symbol"]), market)
                    key = (symbol, market)
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append(WatchItem(symbol=symbol, market=market))
                else:
                    symbol = self._normalize_symbol(str(entry), "unknown")
                    key = (symbol, "unknown")
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append(WatchItem(symbol=symbol, market="unknown"))

        active_markets = {
            market.lower()
            for market in self._settings.active_markets
            if isinstance(market, str) and market.strip()
        }

        if active_markets:
            items = [item for item in items if item.market.lower() in active_markets]

        return Watchlist(items=items)

    def _normalize_symbol(self, symbol: str, market: str) -> str:
        """Normalize symbols across markets.

        - HK: ensure 5-digit numeric code followed by .HK (e.g., 700.HK, 0700.HK, 00700.HK -> 00700.HK; 2800.HK -> 02800.HK)
        - SZ/SH/US: return as-is
        - If symbol already contains a suffix, respect it; otherwise infer from `market`.
        """
        sym = symbol.strip().upper()
        mkt = market.strip().lower()

        # Parse inline market suffix if provided
        if "." in sym:
            head, tail = sym.split(".", 1)
            inferred_market = tail.lower()
            if inferred_market == "hk":
                return f"{self._zfill_digits(head, 5)}.HK"
            # For other markets, return as-is (could add more normalization later)
            return f"{head}.{tail.upper()}"

        # No suffix provided; infer from market parameter
        if mkt == "hk":
            return f"{self._zfill_digits(sym, 5)}.HK"

        return sym

    @staticmethod
    def _zfill_digits(text: str, width: int) -> str:
        digits = ''.join(ch for ch in text if ch.isdigit())
        if not digits:
            return text
        if len(digits) >= width:
            return digits
        return digits.zfill(width)


__all__ = ["WatchItem", "Watchlist", "WatchlistLoader"]
