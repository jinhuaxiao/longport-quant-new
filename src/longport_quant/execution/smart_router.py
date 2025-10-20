"""Smart order routing and execution system."""

from __future__ import annotations

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import asyncio
import math

from loguru import logger
from longport.openapi import TradeContext, OrderSide, OrderType, TimeInForceType
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import OrderRecord, FillRecord, RealtimeQuote
from longport_quant.common.types import Signal
from sqlalchemy import select, and_
from sqlalchemy.dialects.postgresql import insert


class ExecutionStrategy(Enum):
    """Order execution strategies."""
    AGGRESSIVE = "aggressive"  # Market orders, immediate execution
    PASSIVE = "passive"  # Limit orders at favorable prices
    ADAPTIVE = "adaptive"  # Mix based on market conditions
    ICEBERG = "iceberg"  # Hide large order size
    TWAP = "twap"  # Time-weighted average price
    VWAP = "vwap"  # Volume-weighted average price


@dataclass
class OrderRequest:
    """Order request details."""
    symbol: str
    side: str  # BUY or SELL
    quantity: int
    order_type: str  # MARKET or LIMIT
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "DAY"
    strategy: ExecutionStrategy = ExecutionStrategy.ADAPTIVE
    urgency: int = 5  # 1-10, 10 being most urgent
    max_slippage: float = 0.005  # 0.5%
    signal: Optional[Signal] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderSlice:
    """Represents a slice of a larger order."""
    parent_id: str
    slice_number: int
    quantity: int
    order_type: str
    limit_price: Optional[float] = None
    scheduled_time: Optional[datetime] = None
    executed: bool = False
    order_id: Optional[str] = None


@dataclass
class ExecutionResult:
    """Order execution result."""
    success: bool
    order_id: Optional[str] = None
    filled_quantity: int = 0
    average_price: float = 0.0
    slippage: float = 0.0
    commission: float = 0.0
    execution_time: Optional[datetime] = None
    error_message: Optional[str] = None
    child_orders: List[str] = field(default_factory=list)


class SmartOrderRouter:
    """Smart order routing and execution engine."""

    def __init__(
        self,
        trade_context: TradeContext,
        db: DatabaseSessionManager
    ):
        """
        Initialize smart order router.

        Args:
            trade_context: LongPort trade context
            db: Database session manager
        """
        self.trade_context = trade_context
        self.db = db
        self._active_orders: Dict[str, OrderRequest] = {}
        self._order_slices: Dict[str, List[OrderSlice]] = {}
        self._market_data_cache: Dict[str, Dict] = {}

    async def execute_order(self, request: OrderRequest) -> ExecutionResult:
        """
        Execute an order using smart routing.

        Args:
            request: Order request details

        Returns:
            Execution result
        """
        try:
            logger.info(f"Executing order: {request.symbol} {request.side} {request.quantity}")

            # Update market data
            await self._update_market_data(request.symbol)

            # Validate order
            if not await self._validate_order(request):
                return ExecutionResult(
                    success=False,
                    error_message="Order validation failed"
                )

            # Determine execution strategy
            if request.strategy == ExecutionStrategy.ADAPTIVE:
                request.strategy = await self._select_strategy(request)

            # Route order based on strategy
            if request.strategy == ExecutionStrategy.AGGRESSIVE:
                result = await self._execute_aggressive(request)
            elif request.strategy == ExecutionStrategy.PASSIVE:
                result = await self._execute_passive(request)
            elif request.strategy == ExecutionStrategy.ICEBERG:
                result = await self._execute_iceberg(request)
            elif request.strategy == ExecutionStrategy.TWAP:
                result = await self._execute_twap(request)
            elif request.strategy == ExecutionStrategy.VWAP:
                result = await self._execute_vwap(request)
            else:
                result = await self._execute_standard(request)

            # Store execution result
            await self._store_execution_result(request, result)

            return result

        except Exception as e:
            logger.error(f"Order execution failed: {e}")
            return ExecutionResult(
                success=False,
                error_message=str(e)
            )

    async def _validate_order(self, request: OrderRequest) -> bool:
        """Validate order before execution."""
        # Check basic parameters
        if request.quantity <= 0:
            logger.error("Invalid quantity")
            return False

        if request.side not in ["BUY", "SELL"]:
            logger.error("Invalid order side")
            return False

        # Check limit price for limit orders
        if request.order_type == "LIMIT" and not request.limit_price:
            logger.error("Limit price required for limit orders")
            return False

        # Validate against current market price
        market_data = self._market_data_cache.get(request.symbol)
        if market_data:
            current_price = market_data.get('last_price', 0)

            if request.limit_price:
                # Check if limit price is reasonable
                price_diff = abs(request.limit_price - current_price) / current_price
                if price_diff > 0.1:  # More than 10% away from market
                    logger.warning(f"Limit price {request.limit_price} is far from market {current_price}")

        return True

    async def _select_strategy(self, request: OrderRequest) -> ExecutionStrategy:
        """Select appropriate execution strategy based on market conditions."""
        market_data = self._market_data_cache.get(request.symbol, {})

        # High urgency - use aggressive
        if request.urgency >= 8:
            return ExecutionStrategy.AGGRESSIVE

        # Large order - use iceberg or TWAP
        avg_volume = market_data.get('avg_volume', 0)
        if avg_volume > 0:
            order_size_pct = request.quantity / avg_volume
            if order_size_pct > 0.05:  # More than 5% of average volume
                if order_size_pct > 0.1:
                    return ExecutionStrategy.ICEBERG
                else:
                    return ExecutionStrategy.TWAP

        # Low urgency - use passive
        if request.urgency <= 3:
            return ExecutionStrategy.PASSIVE

        # Default to standard execution
        return ExecutionStrategy.AGGRESSIVE

    async def _execute_aggressive(self, request: OrderRequest) -> ExecutionResult:
        """Execute order aggressively using market orders."""
        logger.info(f"Executing aggressive order for {request.symbol}")

        try:
            # Submit market order
            order_side = OrderSide.Buy if request.side == "BUY" else OrderSide.Sell

            resp = await self.trade_context.submit_order(
                symbol=request.symbol,
                side=order_side,
                order_type=OrderType.MO,  # MO = Market Order
                quantity=request.quantity,
                time_in_force=TimeInForceType.Day,
                remark="SmartRouter-Aggressive"
            )

            # Track order
            self._active_orders[resp.order_id] = request

            # Wait for fill
            filled_qty, avg_price = await self._wait_for_fill(resp.order_id, timeout=10)

            # Calculate slippage
            market_data = self._market_data_cache.get(request.symbol, {})
            reference_price = market_data.get('last_price', avg_price)
            slippage = abs(avg_price - reference_price) / reference_price if reference_price > 0 else 0

            return ExecutionResult(
                success=True,
                order_id=resp.order_id,
                filled_quantity=filled_qty,
                average_price=avg_price,
                slippage=slippage,
                execution_time=datetime.now()
            )

        except Exception as e:
            logger.error(f"Aggressive execution failed: {e}")
            return ExecutionResult(success=False, error_message=str(e))

    async def _execute_passive(self, request: OrderRequest) -> ExecutionResult:
        """Execute order passively using limit orders."""
        logger.info(f"Executing passive order for {request.symbol}")

        try:
            market_data = self._market_data_cache.get(request.symbol, {})

            # Determine limit price
            if request.limit_price:
                limit_price = request.limit_price
            else:
                # Place at favorable price
                bid = market_data.get('bid', 0)
                ask = market_data.get('ask', 0)

                if request.side == "BUY":
                    limit_price = bid  # Join the bid
                else:
                    limit_price = ask  # Join the ask

            # Submit limit order
            order_side = OrderSide.Buy if request.side == "BUY" else OrderSide.Sell

            resp = await self.trade_context.submit_order(
                symbol=request.symbol,
                side=order_side,
                order_type=OrderType.LO,  # LO = Limit Order
                price=Decimal(str(limit_price)),
                quantity=request.quantity,
                time_in_force=TimeInForceType.Day,
                remark="SmartRouter-Passive"
            )

            # Track order
            self._active_orders[resp.order_id] = request

            # Wait for fill with longer timeout
            filled_qty, avg_price = await self._wait_for_fill(resp.order_id, timeout=60)

            # If not fully filled, may need to adjust
            if filled_qty < request.quantity:
                logger.warning(f"Partial fill: {filled_qty}/{request.quantity}")

            return ExecutionResult(
                success=True,
                order_id=resp.order_id,
                filled_quantity=filled_qty,
                average_price=avg_price,
                execution_time=datetime.now()
            )

        except Exception as e:
            logger.error(f"Passive execution failed: {e}")
            return ExecutionResult(success=False, error_message=str(e))

    async def _execute_iceberg(self, request: OrderRequest) -> ExecutionResult:
        """Execute large order as iceberg (hidden size)."""
        logger.info(f"Executing iceberg order for {request.symbol}")

        # Calculate slice size (show only 10% at a time)
        visible_size = max(100, request.quantity // 10)
        slices = self._create_order_slices(request, visible_size)

        total_filled = 0
        total_value = 0.0
        child_orders = []

        for slice_order in slices:
            # Execute each slice
            slice_request = OrderRequest(
                symbol=request.symbol,
                side=request.side,
                quantity=slice_order.quantity,
                order_type=request.order_type,
                limit_price=slice_order.limit_price or request.limit_price,
                strategy=ExecutionStrategy.AGGRESSIVE,
                urgency=request.urgency,
                signal=request.signal
            )

            result = await self._execute_aggressive(slice_request)

            if result.success:
                total_filled += result.filled_quantity
                total_value += result.filled_quantity * result.average_price
                child_orders.append(result.order_id)
                slice_order.executed = True
                slice_order.order_id = result.order_id
            else:
                logger.warning(f"Slice execution failed: {result.error_message}")
                break

            # Small delay between slices
            await asyncio.sleep(1)

        avg_price = total_value / total_filled if total_filled > 0 else 0

        return ExecutionResult(
            success=total_filled > 0,
            filled_quantity=total_filled,
            average_price=avg_price,
            execution_time=datetime.now(),
            child_orders=child_orders
        )

    async def _execute_twap(self, request: OrderRequest) -> ExecutionResult:
        """Execute order using Time-Weighted Average Price strategy."""
        logger.info(f"Executing TWAP order for {request.symbol}")

        # Divide order into time slices (e.g., execute over 30 minutes)
        duration_minutes = 30
        num_slices = min(10, max(3, request.quantity // 1000))
        slice_size = request.quantity // num_slices
        interval_seconds = (duration_minutes * 60) / num_slices

        total_filled = 0
        total_value = 0.0
        child_orders = []

        for i in range(num_slices):
            # Calculate slice quantity
            if i == num_slices - 1:
                # Last slice gets remainder
                slice_qty = request.quantity - total_filled
            else:
                slice_qty = slice_size

            # Execute slice
            slice_request = OrderRequest(
                symbol=request.symbol,
                side=request.side,
                quantity=slice_qty,
                order_type="LIMIT",
                limit_price=request.limit_price,
                strategy=ExecutionStrategy.PASSIVE,
                urgency=request.urgency,
                signal=request.signal
            )

            result = await self._execute_passive(slice_request)

            if result.success:
                total_filled += result.filled_quantity
                total_value += result.filled_quantity * result.average_price
                child_orders.append(result.order_id)
            else:
                logger.warning(f"TWAP slice {i+1}/{num_slices} failed")

            # Wait before next slice
            if i < num_slices - 1:
                await asyncio.sleep(interval_seconds)

        avg_price = total_value / total_filled if total_filled > 0 else 0

        return ExecutionResult(
            success=total_filled > 0,
            filled_quantity=total_filled,
            average_price=avg_price,
            execution_time=datetime.now(),
            child_orders=child_orders
        )

    async def _execute_vwap(self, request: OrderRequest) -> ExecutionResult:
        """Execute order using Volume-Weighted Average Price strategy."""
        logger.info(f"Executing VWAP order for {request.symbol}")

        # Get historical volume pattern
        volume_profile = await self._get_volume_profile(request.symbol)

        if not volume_profile:
            # Fallback to TWAP if no volume profile
            return await self._execute_twap(request)

        total_filled = 0
        total_value = 0.0
        child_orders = []

        # Execute based on volume profile
        for time_slot, volume_pct in volume_profile.items():
            # Calculate slice size based on volume percentage
            slice_qty = int(request.quantity * volume_pct)

            if slice_qty <= 0:
                continue

            # Execute slice
            slice_request = OrderRequest(
                symbol=request.symbol,
                side=request.side,
                quantity=slice_qty,
                order_type=request.order_type,
                limit_price=request.limit_price,
                strategy=ExecutionStrategy.ADAPTIVE,
                urgency=request.urgency,
                signal=request.signal
            )

            result = await self._execute_standard(slice_request)

            if result.success:
                total_filled += result.filled_quantity
                total_value += result.filled_quantity * result.average_price
                child_orders.append(result.order_id)

            # Wait for next time slot
            await asyncio.sleep(60)  # 1 minute intervals

        avg_price = total_value / total_filled if total_filled > 0 else 0

        return ExecutionResult(
            success=total_filled > 0,
            filled_quantity=total_filled,
            average_price=avg_price,
            execution_time=datetime.now(),
            child_orders=child_orders
        )

    async def _execute_standard(self, request: OrderRequest) -> ExecutionResult:
        """Execute standard order."""
        market_data = self._market_data_cache.get(request.symbol, {})
        spread = market_data.get('ask', 0) - market_data.get('bid', 0)

        # Use limit order if spread is wide
        if spread > 0 and market_data.get('last_price', 0) > 0:
            spread_pct = spread / market_data['last_price']
            if spread_pct > 0.002:  # Spread > 0.2%
                return await self._execute_passive(request)

        # Otherwise use market order
        return await self._execute_aggressive(request)

    async def _update_market_data(self, symbol: str) -> None:
        """Update cached market data."""
        try:
            # Get real-time quote
            async with self.db.session() as session:
                stmt = select(RealtimeQuote).where(
                    RealtimeQuote.symbol == symbol
                ).order_by(RealtimeQuote.timestamp.desc()).limit(1)

                result = await session.execute(stmt)
                quote = result.scalar_one_or_none()

                if quote:
                    self._market_data_cache[symbol] = {
                        'last_price': float(quote.last_done) if quote.last_done else 0,
                        'bid': float(quote.bid_price) if quote.bid_price else 0,
                        'ask': float(quote.ask_price) if quote.ask_price else 0,
                        'bid_volume': quote.bid_volume,
                        'ask_volume': quote.ask_volume,
                        'volume': quote.volume,
                        'timestamp': quote.timestamp
                    }

                    # Estimate average volume (simplified)
                    self._market_data_cache[symbol]['avg_volume'] = quote.volume / 4  # Assume 4 hours into trading

        except Exception as e:
            logger.error(f"Failed to update market data: {e}")

    async def _wait_for_fill(
        self,
        order_id: str,
        timeout: int = 30
    ) -> Tuple[int, float]:
        """Wait for order to be filled."""
        start_time = datetime.now()
        filled_quantity = 0
        total_value = 0.0

        while (datetime.now() - start_time).seconds < timeout:
            try:
                # Check order status
                orders = await self.trade_context.today_orders()

                for order in orders:
                    if order.order_id == order_id:
                        if order.status in ["FilledStatus", "PartiallyFilledStatus"]:
                            filled_quantity = order.filled_quantity
                            # Calculate average price from executed value
                            if order.filled_quantity > 0:
                                avg_price = float(order.filled_amount) / order.filled_quantity
                                return filled_quantity, avg_price
                        elif order.status in ["RejectedStatus", "CancelledStatus", "ExpiredStatus"]:
                            logger.warning(f"Order {order_id} status: {order.status}")
                            return 0, 0.0

            except Exception as e:
                logger.error(f"Error checking order status: {e}")

            await asyncio.sleep(1)

        logger.warning(f"Timeout waiting for order {order_id} to fill")
        return filled_quantity, total_value / filled_quantity if filled_quantity > 0 else 0

    def _create_order_slices(
        self,
        request: OrderRequest,
        slice_size: int
    ) -> List[OrderSlice]:
        """Create order slices for large orders."""
        slices = []
        remaining = request.quantity
        slice_num = 0

        while remaining > 0:
            qty = min(slice_size, remaining)

            slice_order = OrderSlice(
                parent_id=f"{request.symbol}_{datetime.now().timestamp()}",
                slice_number=slice_num,
                quantity=qty,
                order_type=request.order_type,
                limit_price=request.limit_price
            )

            slices.append(slice_order)
            remaining -= qty
            slice_num += 1

        return slices

    async def _get_volume_profile(self, symbol: str) -> Dict[str, float]:
        """Get historical volume profile for VWAP execution."""
        # Simplified volume profile (in production, would analyze historical data)
        # Returns percentage of daily volume typically traded in each time period
        return {
            "09:30-10:00": 0.15,
            "10:00-10:30": 0.12,
            "10:30-11:00": 0.10,
            "11:00-11:30": 0.08,
            "11:30-12:00": 0.07,
            "12:00-13:00": 0.08,
            "13:00-13:30": 0.08,
            "13:30-14:00": 0.10,
            "14:00-14:30": 0.10,
            "14:30-15:00": 0.12
        }

    async def _store_execution_result(
        self,
        request: OrderRequest,
        result: ExecutionResult
    ) -> None:
        """Store execution result in database."""
        if not result.success:
            return

        try:
            async with self.db.session() as session:
                # Store main order
                order_data = {
                    'order_id': result.order_id,
                    'symbol': request.symbol,
                    'side': request.side,
                    'quantity': request.quantity,
                    'price': result.average_price,
                    'status': 'FILLED' if result.filled_quantity == request.quantity else 'PARTIAL',
                    'created_at': result.execution_time or datetime.now(),
                    'updated_at': datetime.now()
                }

                stmt = insert(OrderRecord).values(order_data)
                stmt = stmt.on_conflict_do_update(
                    index_elements=['order_id'],
                    set_={'status': stmt.excluded.status, 'updated_at': stmt.excluded.updated_at}
                )
                await session.execute(stmt)

                # Store fills
                if result.filled_quantity > 0:
                    fill_data = {
                        'order_id': result.order_id,
                        'trade_id': f"{result.order_id}_001",
                        'symbol': request.symbol,
                        'quantity': result.filled_quantity,
                        'price': result.average_price,
                        'filled_at': result.execution_time or datetime.now()
                    }

                    fill_stmt = insert(FillRecord).values(fill_data)
                    await session.execute(fill_stmt)

                await session.commit()

        except Exception as e:
            logger.error(f"Failed to store execution result: {e}")

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an active order."""
        try:
            await self.trade_context.cancel_order(order_id)

            # Remove from active orders
            self._active_orders.pop(order_id, None)

            logger.info(f"Order {order_id} cancelled")
            return True

        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def modify_order(
        self,
        order_id: str,
        new_quantity: Optional[int] = None,
        new_price: Optional[float] = None
    ) -> bool:
        """Modify an existing order."""
        try:
            await self.trade_context.modify_order(
                order_id=order_id,
                quantity=new_quantity,
                price=Decimal(str(new_price)) if new_price else None
            )

            logger.info(f"Order {order_id} modified")
            return True

        except Exception as e:
            logger.error(f"Failed to modify order {order_id}: {e}")
            return False

    def calculate_optimal_slice_size(
        self,
        total_quantity: int,
        avg_volume: int,
        urgency: int
    ) -> int:
        """Calculate optimal order slice size."""
        # Base slice size on percentage of average volume
        base_pct = 0.01 if urgency < 5 else 0.02 if urgency < 8 else 0.05

        slice_size = int(avg_volume * base_pct)

        # Ensure reasonable bounds
        min_slice = 100
        max_slice = total_quantity // 3  # At least 3 slices

        return max(min_slice, min(slice_size, max_slice))