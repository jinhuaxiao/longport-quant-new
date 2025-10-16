"""订单数据库管理模块"""

from __future__ import annotations

import asyncio
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Set
from decimal import Decimal
import os

from loguru import logger
from sqlalchemy import and_, or_, select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from longport import openapi

from longport_quant.persistence.models import OrderRecord


class OrderManager:
    """订单持久化管理器"""

    def __init__(self, session_factory: Optional[async_sessionmaker] = None):
        """初始化订单管理器

        Args:
            session_factory: 异步会话工厂，如果不提供则创建默认的
        """
        if session_factory:
            self.session_factory = session_factory
        else:
            # 从环境变量获取数据库连接字符串
            db_url = os.getenv('DATABASE_DSN', 'postgresql+asyncpg://postgres:jinhua@127.0.0.1:5432/longport_next_new')
            engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
            self.session_factory = async_sessionmaker(engine, expire_on_commit=False)

        self._today_cache: Dict[str, OrderRecord] = {}  # 今日订单缓存
        self._cache_updated_at = None

    async def save_order(
        self,
        order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        status: str,
        created_at: Optional[datetime] = None
    ) -> OrderRecord:
        """保存订单到数据库

        Args:
            order_id: 订单ID
            symbol: 标的代码
            side: 买卖方向 (BUY/SELL)
            quantity: 数量
            price: 价格
            status: 订单状态
            created_at: 创建时间

        Returns:
            保存的订单记录
        """
        async with self.session_factory() as session:
            # 检查是否已存在
            stmt = select(OrderRecord).where(OrderRecord.order_id == order_id)
            result = await session.execute(stmt)
            existing_order = result.scalar_one_or_none()

            if existing_order:
                # 更新现有订单
                existing_order.status = status
                existing_order.updated_at = datetime.now()
                logger.debug(f"更新订单 {order_id}: {status}")
            else:
                # 创建新订单
                order = OrderRecord(
                    order_id=order_id,
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=price,
                    status=status,
                    created_at=created_at or datetime.now(),
                    updated_at=datetime.now()
                )
                session.add(order)
                logger.info(f"保存新订单 {order_id}: {symbol} {side} {quantity}@{price}")

            await session.commit()

            # 更新缓存
            if existing_order:
                self._today_cache[order_id] = existing_order
                return existing_order
            else:
                self._today_cache[order_id] = order
                return order

    async def update_order_status(self, order_id: str, status: str) -> bool:
        """更新订单状态

        Args:
            order_id: 订单ID
            status: 新状态

        Returns:
            是否更新成功
        """
        async with self.session_factory() as session:
            stmt = update(OrderRecord).where(
                OrderRecord.order_id == order_id
            ).values(
                status=status,
                updated_at=datetime.now()
            )
            result = await session.execute(stmt)
            await session.commit()

            # 更新缓存
            if order_id in self._today_cache:
                self._today_cache[order_id].status = status

            return result.rowcount > 0

    async def get_today_orders(self, symbol: Optional[str] = None) -> List[OrderRecord]:
        """获取今日订单

        Args:
            symbol: 标的代码（可选）

        Returns:
            今日订单列表
        """
        today = date.today()
        today_start = datetime.combine(today, datetime.min.time())

        async with self.session_factory() as session:
            stmt = select(OrderRecord).where(
                OrderRecord.created_at >= today_start
            )

            if symbol:
                stmt = stmt.where(OrderRecord.symbol == symbol)

            result = await session.execute(stmt)
            orders = result.scalars().all()

            # 更新缓存
            for order in orders:
                self._today_cache[order.order_id] = order

            return orders

    async def has_today_order(self, symbol: str, side: str = "BUY") -> bool:
        """检查今日是否有指定标的的订单

        Args:
            symbol: 标的代码
            side: 买卖方向

        Returns:
            是否有订单
        """
        today_orders = await self.get_today_orders(symbol)

        for order in today_orders:
            if order.side == side and order.status in ["Filled", "PartialFilled", "New", "WaitToNew"]:
                return True

        return False

    async def get_today_buy_symbols(self) -> Set[str]:
        """获取今日已买入或待成交的标的列表

        Returns:
            标的代码集合
        """
        today_orders = await self.get_today_orders()

        symbols = set()
        for order in today_orders:
            if order.side == "BUY" and order.status in ["Filled", "PartialFilled", "New", "WaitToNew"]:
                symbols.add(order.symbol)

        return symbols

    async def get_today_sell_symbols(self) -> Set[str]:
        """获取今日已卖出或待成交的标的列表（用于SELL信号去重）

        Returns:
            标的代码集合
        """
        today_orders = await self.get_today_orders()

        symbols = set()
        for order in today_orders:
            if order.side == "SELL" and order.status in ["Filled", "PartialFilled", "New", "WaitToNew"]:
                symbols.add(order.symbol)

        return symbols

    async def sync_with_broker(self, trade_client) -> Dict[str, List[str]]:
        """与券商同步订单状态

        Args:
            trade_client: 交易客户端

        Returns:
            同步结果字典 {"executed": [...], "pending": [...]}
        """
        try:
            # 获取券商今日订单
            broker_orders = await trade_client.today_orders()

            executed_symbols = []
            pending_symbols = []

            for order in broker_orders:
                # 转换状态为字符串
                status_str = str(order.status).replace("OrderStatus.", "")

                # 保存/更新数据库
                await self.save_order(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side="BUY" if order.side == openapi.OrderSide.Buy else "SELL",
                    quantity=order.quantity,
                    price=float(order.price) if order.price else 0,
                    status=status_str,
                    created_at=order.create_time if hasattr(order, 'create_time') else datetime.now()
                )

                # 分类
                if order.side == openapi.OrderSide.Buy:
                    if order.status in [openapi.OrderStatus.Filled, openapi.OrderStatus.PartialFilled]:
                        executed_symbols.append(order.symbol)
                    elif order.status in [openapi.OrderStatus.New, openapi.OrderStatus.WaitToNew]:
                        pending_symbols.append(order.symbol)

            logger.info(f"同步完成: {len(executed_symbols)}个已成交, {len(pending_symbols)}个待成交")

            return {
                "executed": executed_symbols,
                "pending": pending_symbols
            }

        except Exception as e:
            logger.error(f"同步订单失败: {e}")
            return {"executed": [], "pending": []}

    async def cleanup_old_orders(self, days: int = 7):
        """清理旧订单

        Args:
            days: 保留天数
        """
        cutoff_date = datetime.now() - timedelta(days=days)

        async with self.session_factory() as session:
            stmt = delete(OrderRecord).where(
                OrderRecord.created_at < cutoff_date
            )
            result = await session.execute(stmt)
            await session.commit()

            if result.rowcount > 0:
                logger.info(f"清理了 {result.rowcount} 条{days}天前的订单记录")