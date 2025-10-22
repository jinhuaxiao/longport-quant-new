"""
Redis队列管理API

功能：
1. 获取队列统计信息
2. 清空队列
3. 重试失败的信号
"""

import redis
from typing import Dict, List
from loguru import logger


class QueueManager:
    """Redis队列管理器"""

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        """
        初始化队列管理器

        Args:
            redis_url: Redis连接URL
        """
        self.redis_client = redis.from_url(redis_url, decode_responses=True)

        # 队列键名（与SignalQueue保持一致）
        self.queue_key = "trading:signals"
        self.processing_key = "trading:signals:processing"
        self.failed_key = "trading:signals:failed"
        self.stats_key = "trading:signals:stats"

    def get_queue_stats(self) -> Dict[str, any]:
        """
        获取队列统计信息

        Returns:
            队列状态字典
        """
        try:
            pending = self.redis_client.zcard(self.queue_key)
            processing = self.redis_client.zcard(self.processing_key)
            failed = self.redis_client.zcard(self.failed_key)

            # 从stats键获取总处理数和成功率
            stats = self.redis_client.hgetall(self.stats_key)
            total_processed = int(stats.get('total_processed', 0))
            total_success = int(stats.get('total_success', 0))

            success_rate = (total_success / total_processed * 100) if total_processed > 0 else 100.0

            return {
                "pending": pending,
                "processing": processing,
                "failed": failed,
                "total_processed": total_processed,
                "success_rate": success_rate
            }

        except Exception as e:
            logger.error(f"Failed to get queue stats: {e}")
            return {
                "pending": 0,
                "processing": 0,
                "failed": 0,
                "total_processed": 0,
                "success_rate": 100.0,
                "error": str(e)
            }

    def clear_queue(self, queue_type: str) -> Dict[str, any]:
        """
        清空指定的队列

        Args:
            queue_type: 队列类型 ('pending', 'processing', 'failed')

        Returns:
            操作结果
        """
        queue_map = {
            'pending': self.queue_key,
            'processing': self.processing_key,
            'failed': self.failed_key
        }

        if queue_type not in queue_map:
            return {
                "success": False,
                "message": f"Invalid queue type: {queue_type}"
            }

        try:
            key = queue_map[queue_type]
            count = self.redis_client.zcard(key)
            self.redis_client.delete(key)

            logger.info(f"Cleared {count} items from {queue_type} queue")

            return {
                "success": True,
                "message": f"Cleared {count} items from {queue_type} queue",
                "count": count
            }

        except Exception as e:
            logger.error(f"Failed to clear {queue_type} queue: {e}")
            return {
                "success": False,
                "message": f"Failed to clear queue: {str(e)}"
            }

    def retry_failed(self) -> Dict[str, any]:
        """
        将失败的信号重新放入待处理队列

        Returns:
            操作结果
        """
        try:
            # 获取所有失败的信号
            failed_signals = self.redis_client.zrange(self.failed_key, 0, -1, withscores=True)

            if not failed_signals:
                return {
                    "success": True,
                    "message": "No failed signals to retry",
                    "count": 0
                }

            # 将失败的信号移回主队列
            pipe = self.redis_client.pipeline()
            for signal, score in failed_signals:
                # 重新添加到主队列（使用当前时间戳作为分数）
                import time
                pipe.zadd(self.queue_key, {signal: time.time()})

            # 清空失败队列
            pipe.delete(self.failed_key)
            pipe.execute()

            count = len(failed_signals)
            logger.info(f"Retried {count} failed signals")

            return {
                "success": True,
                "message": f"Retried {count} failed signals",
                "count": count
            }

        except Exception as e:
            logger.error(f"Failed to retry failed signals: {e}")
            return {
                "success": False,
                "message": f"Failed to retry: {str(e)}"
            }

    def get_recent_signals(self, limit: int = 10) -> List[Dict[str, any]]:
        """
        获取最近的信号（从待处理队列）

        Args:
            limit: 返回数量限制

        Returns:
            信号列表
        """
        try:
            # 从队列获取最新的信号（按时间戳倒序）
            signals = self.redis_client.zrevrange(
                self.queue_key,
                0,
                limit - 1,
                withscores=True
            )

            import json
            from datetime import datetime

            result = []
            for signal_str, timestamp in signals:
                try:
                    signal_data = json.loads(signal_str)
                    result.append({
                        "timestamp": datetime.fromtimestamp(timestamp).isoformat(),
                        "symbol": signal_data.get("symbol"),
                        "action": signal_data.get("action"),
                        "score": signal_data.get("score"),
                        "price": signal_data.get("price")
                    })
                except json.JSONDecodeError:
                    continue

            return result

        except Exception as e:
            logger.error(f"Failed to get recent signals: {e}")
            return []

    def clear_all_queues(self) -> Dict[str, any]:
        """
        清空所有队列（危险操作）

        Returns:
            操作结果
        """
        try:
            keys = [self.queue_key, self.processing_key, self.failed_key]
            total_count = sum(self.redis_client.zcard(key) for key in keys)

            self.redis_client.delete(*keys)

            logger.warning(f"Cleared all queues: {total_count} total items")

            return {
                "success": True,
                "message": f"Cleared all queues ({total_count} items)",
                "count": total_count
            }

        except Exception as e:
            logger.error(f"Failed to clear all queues: {e}")
            return {
                "success": False,
                "message": f"Failed to clear: {str(e)}"
            }
