"""Trading utility functions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from longport_quant.data.quote_client import QuoteDataClient


class LotSizeHelper:
    """Helper class for managing lot size information and calculating valid order quantities."""

    def __init__(self):
        self._lot_size_cache = {}

    async def get_lot_size(self, symbol: str, quote_client: QuoteDataClient) -> int:
        """
        Get the lot size (board lot) for a given symbol.

        Args:
            symbol: The stock symbol (e.g., "1398.HK", "AAPL.US")
            quote_client: QuoteDataClient instance for API calls

        Returns:
            The lot size for the symbol (number of shares per lot)
        """
        # Check cache first
        if symbol in self._lot_size_cache:
            return self._lot_size_cache[symbol]

        try:
            # Fetch static info from API
            static_info = await quote_client.get_static_info([symbol])
            if static_info and len(static_info) > 0:
                info = static_info[0]
                # 优先使用 board_lot，其次兼容旧字段 lot_size
                lot_fields = [
                    getattr(info, 'board_lot', None),
                    getattr(info, 'lot_size', None)
                ]
                for lot_size in lot_fields:
                    if lot_size and lot_size > 0:
                        self._lot_size_cache[symbol] = lot_size
                        logger.debug(f"获取 {symbol} 手数: {lot_size}")
                        return lot_size

            # Default lot sizes based on market
            default_lot_size = 1 if ".US" in symbol else 100
            logger.warning(f"无法获取 {symbol} 的手数信息，使用默认值: {default_lot_size}")
            self._lot_size_cache[symbol] = default_lot_size
            return default_lot_size

        except Exception as e:
            logger.error(f"获取 {symbol} 手数信息失败: {e}")
            # Default lot sizes based on market
            default_lot_size = 1 if ".US" in symbol else 100
            self._lot_size_cache[symbol] = default_lot_size
            return default_lot_size

    def calculate_order_quantity(
        self,
        symbol: str,
        budget: float,
        price: float,
        lot_size: int
    ) -> int:
        """
        Calculate the valid order quantity based on budget, price, and lot size.

        The quantity must be a multiple of the lot size.

        Args:
            symbol: The stock symbol
            budget: Maximum budget to spend
            price: Current stock price
            lot_size: The lot size for the stock

        Returns:
            Valid order quantity (multiple of lot_size), or 0 if budget is insufficient
        """
        if price <= 0:
            logger.warning(f"{symbol}: 价格无效 (${price})")
            return 0

        if lot_size <= 0:
            logger.warning(f"{symbol}: 手数无效 ({lot_size})")
            return 0

        # Calculate how many shares we can afford
        affordable_shares = int(budget / price)

        # Round down to nearest multiple of lot_size
        num_lots = affordable_shares // lot_size
        quantity = num_lots * lot_size

        if quantity <= 0:
            logger.debug(
                f"{symbol}: 预算不足以购买1手 "
                f"(手数: {lot_size}, 需要: ${lot_size * price:.2f}, "
                f"预算: ${budget:.2f})"
            )
            return 0

        logger.debug(
            f"{symbol}: 计算订单数量 = {quantity}股 "
            f"({num_lots}手 × {lot_size}股/手)"
        )
        return quantity


def calculate_order_quantity_simple(
    budget: float,
    price: float,
    lot_size: int = 1
) -> int:
    """
    Simple function to calculate order quantity.

    Args:
        budget: Maximum budget to spend
        price: Current stock price
        lot_size: The lot size for the stock (default: 1 for US stocks)

    Returns:
        Valid order quantity (multiple of lot_size), or 0 if budget is insufficient
    """
    helper = LotSizeHelper()
    return helper.calculate_order_quantity("", budget, price, lot_size)


__all__ = ["LotSizeHelper", "calculate_order_quantity_simple"]
