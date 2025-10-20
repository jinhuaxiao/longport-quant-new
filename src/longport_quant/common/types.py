"""Common types used across the system."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass
class Signal:
    """Trading signal."""
    symbol: str
    side: str
    quantity: float
    price: float


class OrderSide(Enum):
    """Order side."""
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    """Order status."""
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"