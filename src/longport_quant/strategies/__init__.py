"""Built-in strategy implementations."""

from .sample import SampleStrategy
from .watchlist_auto import AutoTradeStrategy

__all__ = ["SampleStrategy", "AutoTradeStrategy"]
