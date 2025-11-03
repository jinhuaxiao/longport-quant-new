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

    async def get_orders_by_date_range(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        side: Optional[str] = None
    ) -> List[OrderRecord]:
        """按日期范围查询订单

        Args:
            start_date: 开始时间（包含），默认为7天前
            end_date: 结束时间（包含），默认为今天结束
            symbol: 标的代码（可选）
            status: 订单状态（可选）
            side: 买卖方向（可选）

        Returns:
            订单记录列表
        """
        # 设置默认日期范围
        if start_date is None:
            start_date = datetime.now() - timedelta(days=7)
        if end_date is None:
            end_date = datetime.combine(date.today(), datetime.max.time())

        async with self.session_factory() as session:
            stmt = select(OrderRecord).where(
                and_(
                    OrderRecord.created_at >= start_date,
                    OrderRecord.created_at <= end_date
                )
            )

            if symbol:
                stmt = stmt.where(OrderRecord.symbol == symbol)
            if status:
                stmt = stmt.where(OrderRecord.status == status)
            if side:
                stmt = stmt.where(OrderRecord.side == side)

            stmt = stmt.order_by(OrderRecord.created_at.desc())

            result = await session.execute(stmt)
            orders = result.scalars().all()

            return orders

    async def get_old_orders(
        self,
        keep_days: int = 1,
        symbol: Optional[str] = None
    ) -> List[OrderRecord]:
        """获取历史订单（超过keep_days天的订单）

        Args:
            keep_days: 保留天数，默认1天（只保留今日）
            symbol: 标的代码（可选）

        Returns:
            历史订单列表
        """
        cutoff_date = datetime.combine(
            date.today() - timedelta(days=keep_days - 1),
            datetime.min.time()
        )

        async with self.session_factory() as session:
            stmt = select(OrderRecord).where(
                OrderRecord.created_at < cutoff_date
            )

            if symbol:
                stmt = stmt.where(OrderRecord.symbol == symbol)

            stmt = stmt.order_by(OrderRecord.created_at.desc())

            result = await session.execute(stmt)
            orders = result.scalars().all()

            return orders

    async def cancel_old_orders(
        self,
        trade_client,
        keep_days: int = 1,
        dry_run: bool = False,
        cancelable_statuses: Optional[List[str]] = None
    ) -> Dict[str, any]:
        """批量取消历史订单（从券商端）

        Args:
            trade_client: 交易客户端实例
            keep_days: 保留天数，默认1天（只保留今日）
            dry_run: 是否只预览不执行（默认False）
            cancelable_statuses: 可取消的订单状态列表，默认包含：
                - New: 新订单
                - PartialFilled: 部分成交
                - WaitToNew: 等待提交
                - VarietiesNotReported: 品种未报告（GTC条件单常见状态）
                - NotReported: 未报告

        Returns:
            结果字典:
            {
                "total_found": 查询到的历史订单总数,
                "cancelable": 可取消的订单数,
                "cancelled": 实际取消的订单数,
                "failed": 取消失败的订单数,
                "dry_run": 是否为预览模式,
                "orders": 订单详情列表,
                "cancel_result": 批量取消结果（如果执行了）
            }
        """
        if cancelable_statuses is None:
            # 默认包含所有可取消状态，包括 VarietiesNotReported 和 NotReported
            cancelable_statuses = ["New", "PartialFilled", "WaitToNew", "VarietiesNotReported", "NotReported"]

        # 计算日期范围
        today = date.today()
        cutoff_date = datetime.combine(
            today - timedelta(days=keep_days - 1),
            datetime.min.time()
        )

        logger.info(f"查询 {cutoff_date.strftime('%Y-%m-%d')} 之前的订单...")

        all_orders = []

        # 1. 查询今日订单（GTC订单会一直显示在今日订单中）
        try:
            today_orders = await trade_client.today_orders()
            logger.info(f"从券商查询到 {len(today_orders)} 个今日订单")

            # 过滤出创建时间在cutoff_date之前的订单
            for order in today_orders:
                # 获取订单创建时间
                order_time = None
                if hasattr(order, 'submitted_at') and order.submitted_at:
                    order_time = order.submitted_at
                elif hasattr(order, 'created_at') and order.created_at:
                    order_time = order.created_at

                # 如果订单创建时间早于cutoff_date，加入列表
                if order_time and order_time.replace(tzinfo=None) < cutoff_date:
                    all_orders.append(order)

            logger.info(f"今日订单中有 {len(all_orders)} 个创建于 {cutoff_date.strftime('%Y-%m-%d')} 之前")

        except Exception as e:
            logger.error(f"查询今日订单失败: {e}")

        # 2. 查询历史订单（已结束的订单）
        try:
            history_orders = await trade_client.history_orders(
                start_at=cutoff_date - timedelta(days=30),  # 查询最近30天
                end_at=cutoff_date - timedelta(seconds=1)  # 不包含cutoff_date当天
            )

            logger.info(f"从券商查询到 {len(history_orders)} 个历史订单")
            all_orders.extend(history_orders)

        except Exception as e:
            logger.error(f"查询券商历史订单失败: {e}")

        # 过滤可取消的订单
        cancelable_orders = []
        order_details = []

        for order in all_orders:
            status_str = str(order.status).replace("OrderStatus.", "")
            order_info = {
                "order_id": order.order_id,
                "symbol": order.symbol,
                "side": "BUY" if order.side == openapi.OrderSide.Buy else "SELL",
                "quantity": order.quantity,
                "price": float(order.price) if hasattr(order, 'price') and order.price else 0,
                "status": status_str,
                "created_at": order.created_at if hasattr(order, 'created_at') else None
            }
            order_details.append(order_info)

            if status_str in cancelable_statuses:
                cancelable_orders.append(order.order_id)

        logger.info(
            f"可取消订单: {len(cancelable_orders)}/{len(all_orders)} "
            f"(状态: {', '.join(cancelable_statuses)})"
        )

        # 如果是预览模式，返回详情
        if dry_run:
            logger.info("【预览模式】不执行实际取消操作")
            return {
                "total_found": len(all_orders),
                "cancelable": len(cancelable_orders),
                "cancelled": 0,
                "failed": 0,
                "dry_run": True,
                "orders": order_details,
                "cancel_result": None
            }

        # 执行批量取消
        cancel_result = None
        if len(cancelable_orders) > 0:
            logger.info(f"开始批量取消 {len(cancelable_orders)} 个订单...")
            cancel_result = await trade_client.cancel_orders_batch(cancelable_orders)

            # 更新数据库中的订单状态
            for order_id in cancel_result.get("success_ids", []):
                await self.update_order_status(order_id, "Canceled")

        return {
            "total_found": len(all_orders),
            "cancelable": len(cancelable_orders),
            "cancelled": cancel_result.get("succeeded", 0) if cancel_result else 0,
            "failed": cancel_result.get("failed", 0) if cancel_result else 0,
            "dry_run": False,
            "orders": order_details,
            "cancel_result": cancel_result
        }