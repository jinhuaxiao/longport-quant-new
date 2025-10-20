"""
持仓管理器 - 使用Redis实现跨进程共享持仓状态

这个模块解决了重复开仓的核心问题：
- signal_generator 和 order_executor 是独立进程
- 需要实时共享当前持仓状态
- 避免持仓更新延迟导致的重复买入

架构：
- 使用 Redis SET 存储当前持仓标的
- 使用 Redis Pub/Sub 实现实时通知
- 订单成交后立即更新，延迟<1秒
"""

import json
from typing import Set, Optional, Dict, Any
from datetime import datetime
from zoneinfo import ZoneInfo
import redis.asyncio as redis
from loguru import logger


class RedisPositionManager:
    """
    Redis持仓管理器 - 跨进程共享持仓状态

    功能：
    1. 存储当前持仓标的（Redis SET）
    2. 提供原子操作的添加/删除/检查
    3. 支持Redis Pub/Sub实时通知
    4. 自动同步和定期刷新
    """

    def __init__(self, redis_url: str, key_prefix: str = "trading"):
        """
        初始化持仓管理器

        Args:
            redis_url: Redis连接URL
            key_prefix: Redis键前缀（用于隔离不同环境）
        """
        self.redis_url = redis_url
        self.key_prefix = key_prefix

        # Redis键名
        self.positions_key = f"{key_prefix}:current_positions"  # SET: 当前持仓标的
        self.position_details_key = f"{key_prefix}:position_details"  # HASH: 持仓详情
        self.pubsub_channel = f"{key_prefix}:position_updates"  # Pub/Sub频道

        # Redis客户端（延迟初始化）
        self._redis: Optional[redis.Redis] = None
        self._pubsub: Optional[redis.client.PubSub] = None

        self.beijing_tz = ZoneInfo('Asia/Shanghai')

    async def connect(self):
        """建立Redis连接"""
        if self._redis is None:
            self._redis = await redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
            logger.info(f"✅ Redis持仓管理器已连接: {self.positions_key}")

    async def close(self):
        """关闭Redis连接"""
        if self._pubsub:
            await self._pubsub.close()
        if self._redis:
            await self._redis.close()
            logger.info("✅ Redis持仓管理器已关闭")

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()

    # ==================== 核心操作 ====================

    async def add_position(
        self,
        symbol: str,
        quantity: float = 0,
        cost_price: float = 0,
        order_id: str = "",
        notify: bool = True
    ) -> bool:
        """
        添加持仓（买入）

        Args:
            symbol: 标的代码
            quantity: 持仓数量
            cost_price: 成本价
            order_id: 订单ID
            notify: 是否发布Pub/Sub通知

        Returns:
            是否添加成功
        """
        await self.connect()

        try:
            # 1. 添加到持仓集合
            result = await self._redis.sadd(self.positions_key, symbol)

            # 2. 保存持仓详情
            position_data = {
                "symbol": symbol,
                "quantity": quantity,
                "cost_price": cost_price,
                "order_id": order_id,
                "added_at": datetime.now(self.beijing_tz).isoformat(),
            }
            await self._redis.hset(
                self.position_details_key,
                symbol,
                json.dumps(position_data)
            )

            # 3. 发布通知
            if notify:
                await self._publish_update("add", symbol, position_data)

            if result:
                logger.success(f"✅ Redis持仓添加: {symbol} | 数量:{quantity:.0f} | 价格:${cost_price:.2f}")
            else:
                logger.debug(f"ℹ️  Redis持仓已存在: {symbol}")

            return True

        except Exception as e:
            logger.error(f"❌ Redis持仓添加失败: {symbol} - {e}")
            return False

    async def remove_position(
        self,
        symbol: str,
        notify: bool = True
    ) -> bool:
        """
        移除持仓（卖出）

        Args:
            symbol: 标的代码
            notify: 是否发布Pub/Sub通知

        Returns:
            是否移除成功
        """
        await self.connect()

        try:
            # 1. 从持仓集合移除
            result = await self._redis.srem(self.positions_key, symbol)

            # 2. 删除持仓详情
            await self._redis.hdel(self.position_details_key, symbol)

            # 3. 发布通知
            if notify:
                await self._publish_update("remove", symbol, {"symbol": symbol})

            if result:
                logger.success(f"✅ Redis持仓移除: {symbol}")
            else:
                logger.debug(f"ℹ️  Redis持仓不存在: {symbol}")

            return True

        except Exception as e:
            logger.error(f"❌ Redis持仓移除失败: {symbol} - {e}")
            return False

    async def has_position(self, symbol: str) -> bool:
        """
        检查是否持有某个标的

        Args:
            symbol: 标的代码

        Returns:
            是否持有
        """
        await self.connect()

        try:
            result = await self._redis.sismember(self.positions_key, symbol)
            return result
        except Exception as e:
            logger.error(f"❌ Redis持仓检查失败: {symbol} - {e}")
            # 失败时返回False，允许继续（安全模式）
            return False

    async def get_all_positions(self) -> Set[str]:
        """
        获取所有持仓标的

        Returns:
            持仓标的集合
        """
        await self.connect()

        try:
            positions = await self._redis.smembers(self.positions_key)
            return positions if positions else set()
        except Exception as e:
            logger.error(f"❌ Redis获取持仓列表失败: {e}")
            return set()

    async def get_position_detail(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        获取持仓详情

        Args:
            symbol: 标的代码

        Returns:
            持仓详情字典，如果不存在返回None
        """
        await self.connect()

        try:
            data = await self._redis.hget(self.position_details_key, symbol)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"❌ Redis获取持仓详情失败: {symbol} - {e}")
            return None

    async def get_all_position_details(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有持仓详情

        Returns:
            {symbol: position_data} 字典
        """
        await self.connect()

        try:
            all_data = await self._redis.hgetall(self.position_details_key)
            return {
                symbol: json.loads(data)
                for symbol, data in all_data.items()
            }
        except Exception as e:
            logger.error(f"❌ Redis获取所有持仓详情失败: {e}")
            return {}

    async def clear_all_positions(self) -> bool:
        """
        清空所有持仓（谨慎使用！）

        Returns:
            是否清空成功
        """
        await self.connect()

        try:
            await self._redis.delete(self.positions_key)
            await self._redis.delete(self.position_details_key)
            logger.warning("⚠️ Redis持仓已清空")
            return True
        except Exception as e:
            logger.error(f"❌ Redis清空持仓失败: {e}")
            return False

    async def sync_from_api(self, positions: list) -> bool:
        """
        从API同步持仓到Redis（批量更新）

        Args:
            positions: LongPort API返回的持仓列表

        Returns:
            是否同步成功
        """
        await self.connect()

        try:
            # 获取当前Redis中的持仓
            redis_positions = await self.get_all_positions()

            # API返回的持仓
            api_positions = {
                pos["symbol"]
                for pos in positions
                if pos.get("quantity", 0) > 0
            }

            # 找出需要添加和删除的
            to_add = api_positions - redis_positions
            to_remove = redis_positions - api_positions

            # 批量添加
            for symbol in to_add:
                pos = next(p for p in positions if p["symbol"] == symbol)
                await self.add_position(
                    symbol=symbol,
                    quantity=pos.get("quantity", 0),
                    cost_price=pos.get("cost_price", 0),
                    notify=False  # 批量同步不发通知
                )

            # 批量删除
            for symbol in to_remove:
                await self.remove_position(symbol, notify=False)

            logger.info(
                f"✅ Redis持仓同步完成: "
                f"添加{len(to_add)}个, 删除{len(to_remove)}个, "
                f"总计{len(api_positions)}个"
            )

            return True

        except Exception as e:
            logger.error(f"❌ Redis持仓同步失败: {e}")
            return False

    # ==================== Pub/Sub 通知 ====================

    async def _publish_update(
        self,
        action: str,
        symbol: str,
        data: Dict[str, Any]
    ):
        """
        发布持仓更新通知

        Args:
            action: 操作类型（add/remove）
            symbol: 标的代码
            data: 附加数据
        """
        await self.connect()

        try:
            message = {
                "action": action,
                "symbol": symbol,
                "data": data,
                "timestamp": datetime.now(self.beijing_tz).isoformat(),
            }

            await self._redis.publish(
                self.pubsub_channel,
                json.dumps(message)
            )

            logger.debug(f"📢 Redis发布持仓更新: {action} {symbol}")

        except Exception as e:
            logger.error(f"❌ Redis发布通知失败: {e}")

    async def subscribe_updates(self, callback):
        """
        订阅持仓更新通知

        Args:
            callback: 回调函数 async def callback(action, symbol, data)
        """
        await self.connect()

        try:
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(self.pubsub_channel)

            logger.info(f"📡 开始监听持仓更新: {self.pubsub_channel}")

            async for message in self._pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        await callback(
                            data["action"],
                            data["symbol"],
                            data.get("data", {})
                        )
                    except Exception as e:
                        logger.error(f"❌ 处理持仓更新通知失败: {e}")

        except Exception as e:
            logger.error(f"❌ Redis订阅失败: {e}")

    # ==================== 统计和调试 ====================

    async def get_stats(self) -> Dict[str, Any]:
        """
        获取统计信息

        Returns:
            统计数据字典
        """
        await self.connect()

        try:
            positions = await self.get_all_positions()
            details = await self.get_all_position_details()

            return {
                "total_positions": len(positions),
                "positions": sorted(list(positions)),
                "has_details": len(details),
                "redis_key": self.positions_key,
                "pubsub_channel": self.pubsub_channel,
            }
        except Exception as e:
            logger.error(f"❌ 获取Redis统计失败: {e}")
            return {}

    async def print_status(self):
        """打印当前持仓状态（调试用）"""
        stats = await self.get_stats()

        print("\n" + "="*70)
        print("📊 Redis持仓状态")
        print("="*70)
        print(f"Redis键: {stats.get('redis_key', 'N/A')}")
        print(f"持仓数量: {stats.get('total_positions', 0)}")

        if stats.get('positions'):
            print(f"\n持仓列表:")
            for symbol in stats['positions']:
                detail = await self.get_position_detail(symbol)
                if detail:
                    print(f"  {symbol}: {detail.get('quantity', 0):.0f}股 @ ${detail.get('cost_price', 0):.2f}")
                else:
                    print(f"  {symbol}")
        else:
            print("\n当前无持仓")

        print("="*70 + "\n")
