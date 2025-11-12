"""信号队列管理（基于Redis ZSET实现优先级队列）"""

import json
import time
from typing import Dict, List, Optional
from decimal import Decimal
from datetime import datetime
from loguru import logger

try:
    import redis.asyncio as aioredis
except ImportError:
    logger.warning("redis.asyncio not available, falling back to redis")
    import redis


class SignalQueue:
    """
    基于Redis的异步信号队列

    使用Redis ZSET实现优先级队列：
    - key: trading:signals (主队列)
    - score: -priority (负数，越大越优先)
    - value: JSON序列化的信号数据

    特性：
    - 优先级队列（高分信号优先执行）
    - 持久化（Redis AOF）
    - 原子操作（避免竞争）
    - 支持重试机制
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        queue_key: str = "trading:signals",
        processing_key: str = "trading:signals:processing",
        failed_key: str = "trading:signals:failed",
        max_retries: int = 3
    ):
        """
        初始化信号队列

        Args:
            redis_url: Redis连接URL
            queue_key: 主队列key
            processing_key: 处理中队列key
            failed_key: 失败队列key
            max_retries: 最大重试次数
        """
        self.redis_url = redis_url
        self.queue_key = queue_key
        self.processing_key = processing_key
        self.failed_key = failed_key
        self.max_retries = max_retries

        # 连接会在第一次使用时创建
        self._redis = None

        # 日志限流：记录上次输出空队列日志的时间
        self._last_empty_log_time = 0
        # 最近一次仅遇到延迟信号时的最短等待提示（秒）
        self._last_delay_hint: Optional[float] = None

    async def _get_redis(self):
        """获取Redis连接（懒加载）"""
        if self._redis is None:
            self._redis = await aioredis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
        return self._redis

    async def close(self):
        """关闭Redis连接"""
        if self._redis:
            await self._redis.close()
            self._redis = None

    def _serialize_signal(self, signal: Dict) -> str:
        """
        序列化信号数据

        将Decimal等特殊类型转换为JSON可序列化的格式
        """
        def decimal_default(obj):
            if isinstance(obj, Decimal):
                return float(obj)
            elif isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

        return json.dumps(signal, default=decimal_default, ensure_ascii=False)

    def _deserialize_signal(self, signal_json: str) -> Dict:
        """反序列化信号数据"""
        return json.loads(signal_json)

    async def publish_signal(
        self,
        signal: Dict,
        priority: Optional[int] = None
    ) -> bool:
        """
        发布信号到队列

        Args:
            signal: 信号数据字典，必须包含:
                - symbol: 标的代码
                - type: 信号类型 (BUY/SELL)
                - score: 评分
                - price: 价格
                其他字段根据需要添加
            priority: 优先级（越大越优先），默认使用signal['score']

        Returns:
            bool: 是否成功发布
        """
        try:
            redis = await self._get_redis()

            # 添加元数据
            signal['queued_at'] = datetime.now().isoformat()
            signal['retry_count'] = signal.get('retry_count', 0)

            # 确定优先级（使用负数，因为ZSET按score升序排列）
            if priority is None:
                priority = signal.get('score', 0)

            # 添加时间戳打破相同优先级的排序（微秒级）
            score = -priority + (time.time() % 1) * 0.00001

            # 序列化信号
            signal_json = self._serialize_signal(signal)

            # 使用ZADD添加到有序集合
            result = await redis.zadd(
                self.queue_key,
                {signal_json: score},
                nx=False  # 允许更新已存在的信号
            )

            logger.debug(
                f"✅ 信号已发布到队列: {signal['symbol']}, "
                f"优先级={priority}, score={score:.6f}, "
                f"队列长度={await self.get_queue_size()}"
            )

            return result is not None

        except Exception as e:
            logger.error(f"❌ 发布信号失败: {e}")
            return False

    async def recover_zombie_signals(self, timeout_seconds: int = 300) -> int:
        """
        恢复僵尸信号（超时未完成的信号）

        Args:
            timeout_seconds: 超时时间（秒），默认5分钟

        Returns:
            int: 恢复的信号数量
        """
        try:
            redis = await self._get_redis()
            current_time = time.time()
            cutoff_time = current_time - timeout_seconds

            # 获取所有处理中的信号
            processing_signals = await redis.zrangebyscore(
                self.processing_key,
                '-inf',
                cutoff_time,
                withscores=True
            )

            if not processing_signals:
                return 0

            recovered_count = 0
            for signal_json, score in processing_signals:
                signal = self._deserialize_signal(signal_json)
                symbol = signal.get('symbol', 'N/A')

                # 计算已经处理的时间
                elapsed_time = current_time - score

                logger.warning(
                    f"🔧 恢复僵尸信号: {symbol}, "
                    f"已卡住 {elapsed_time/60:.1f} 分钟"
                )

                # 从processing队列移除
                await redis.zrem(self.processing_key, signal_json)

                # 重新发布到主队列（保持原优先级）
                original_priority = signal.get('score', 0)
                await self.publish_signal(signal, priority=original_priority)

                recovered_count += 1

            if recovered_count > 0:
                logger.info(f"✅ 成功恢复 {recovered_count} 个僵尸信号")

            return recovered_count

        except Exception as e:
            logger.error(f"❌ 恢复僵尸信号失败: {e}")
            return 0

    async def consume_signal(
        self,
        timeout: Optional[float] = None,
        auto_recover: bool = True,
        signal_ttl_seconds: int = 3600,
        max_delay_seconds: int = 1800
    ) -> Optional[Dict]:
        """
        从队列消费一个信号（优先级最高的）

        Args:
            timeout: 超时时间（秒），None表示立即返回
            auto_recover: 是否自动恢复僵尸信号
            signal_ttl_seconds: 信号过期时间（秒），超过此时间的信号将被丢弃
            max_delay_seconds: 延迟信号最大等待时间（秒），超过此时间的延迟信号将被丢弃

        Returns:
            Dict: 信号数据，如果队列为空返回None
        """
        try:
            redis = await self._get_redis()

            # 自动恢复僵尸信号（每次消费前检查）
            if auto_recover:
                await self.recover_zombie_signals(timeout_seconds=300)

            # 🔥 尝试多次获取可用信号（避免单个信号的无限循环）
            max_attempts = 10  # 最多尝试10次
            skipped_signals = []  # 记录被跳过的信号

            min_wait_seconds: Optional[float] = None

            for attempt in range(max_attempts):
                # 使用ZPOPMIN获取最高优先级（最低负分）的信号
                # 因为score是负数，最小的score（如-65）对应最高的优先级（65）
                result = await redis.zpopmin(self.queue_key, count=1)

                if not result:
                    # 队列为空，将之前跳过的信号放回
                    for sig_json, sig_score in skipped_signals:
                        await redis.zadd(self.queue_key, {sig_json: sig_score})
                    self._last_delay_hint = None
                    return None

                signal_json, score = result[0]
                signal = self._deserialize_signal(signal_json)

                # 🔥 检查信号是否已过期（基于queued_at时间）
                queued_at_str = signal.get('queued_at')
                if queued_at_str:
                    try:
                        queued_at = datetime.fromisoformat(queued_at_str)
                        signal_age = (datetime.now() - queued_at).total_seconds()

                        if signal_age > signal_ttl_seconds:
                            logger.warning(
                                f"⏰ 信号已过期（{signal_age/60:.1f}分钟 > {signal_ttl_seconds/60:.1f}分钟）: "
                                f"{signal.get('symbol')}，直接丢弃"
                            )
                            # 不放回队列，直接丢弃，继续获取下一个
                            continue
                    except Exception as e:
                        logger.warning(f"⚠️ 解析信号时间失败: {e}，跳过此信号")
                        continue

                # 检查是否需要延迟处理
                if 'retry_after' in signal:
                    if time.time() < signal['retry_after']:
                        # 🔥 新增：检查信号总存在时间（防止信号长期停留）
                        if queued_at_str:
                            try:
                                queued_at = datetime.fromisoformat(queued_at_str)
                                total_age = (datetime.now() - queued_at).total_seconds()

                                # 如果信号总存在时间超过TTL，直接丢弃（即使还在延迟期）
                                if total_age > signal_ttl_seconds:
                                    logger.warning(
                                        f"⏰ 延迟信号已存在过久（{total_age/60:.1f}分钟 > {signal_ttl_seconds/60:.1f}分钟），直接丢弃: "
                                        f"{signal.get('symbol')} (retry_after还剩{(signal['retry_after']-time.time())/60:.1f}分钟)"
                                    )
                                    continue
                            except Exception as e:
                                logger.warning(f"⚠️ 解析延迟信号时间失败: {e}")

                        # 🔥 检查剩余延迟时间是否超过最大等待时间
                        delay_duration = signal['retry_after'] - time.time()

                        if delay_duration > max_delay_seconds:
                            logger.warning(
                                f"⏰ 延迟信号超过最大等待时间（{delay_duration/60:.1f}分钟 > {max_delay_seconds/60:.1f}分钟），直接丢弃: "
                                f"{signal.get('symbol')}"
                            )
                            # 不放回队列，直接丢弃
                            continue

                        # 未到重试时间，记录并继续尝试下一个
                        skipped_signals.append((signal_json, score))
                        wait_seconds = max(0.0, signal['retry_after'] - time.time())
                        if min_wait_seconds is None or wait_seconds < min_wait_seconds:
                            min_wait_seconds = wait_seconds

                        # 只在第一次遇到时记录日志（避免刷屏）
                        if len(skipped_signals) == 1:
                            retry_in = signal['retry_after'] - time.time()
                            logger.debug(
                                f"⏰ 信号未到重试时间，尝试获取其他信号: {signal.get('symbol')} "
                                f"(还需等待{retry_in:.0f}秒)"
                            )
                        continue

                # 找到可用信号，将之前跳过的信号放回队列
                for sig_json, sig_score in skipped_signals:
                    await redis.zadd(self.queue_key, {sig_json: sig_score})

                # 保存原始JSON（用于后续删除）
                # ⚠️ 重要：必须使用原始JSON，因为signal对象会被修改
                signal['_original_json'] = signal_json

                # 添加处理时间戳
                signal['processing_started_at'] = datetime.now().isoformat()

                # 移到处理中队列（用于监控和恢复）
                # ⚠️ 使用原始JSON，而非修改后的signal
                await redis.zadd(
                    self.processing_key,
                    {signal_json: time.time()}
                )

                logger.debug(
                    f"📥 从队列消费信号: {signal['symbol']}, "
                    f"优先级={-score:.0f}, "
                    f"剩余队列长度={await self.get_queue_size()}"
                )

                self._last_delay_hint = None
                return signal

            # 🔥 所有信号都未到重试时间，将它们放回队列
            for sig_json, sig_score in skipped_signals:
                await redis.zadd(self.queue_key, {sig_json: sig_score})

            if skipped_signals:
                # 日志限流：最多每30秒记录一次，避免刷屏
                current_time = time.time()
                if current_time - self._last_empty_log_time >= 30:
                    logger.debug(
                        f"⏰ 队列中所有信号({len(skipped_signals)}个)都未到重试时间，暂无可处理信号"
                    )
                    self._last_empty_log_time = current_time

                if min_wait_seconds is not None:
                    self._last_delay_hint = max(0.0, min_wait_seconds)
                else:
                    self._last_delay_hint = None
            else:
                self._last_delay_hint = None

            return None

        except Exception as e:
            logger.error(f"❌ 消费信号失败: {e}")
            return None

    async def mark_signal_completed(self, signal: Dict) -> bool:
        """
        标记信号处理完成

        从processing队列中移除
        """
        try:
            redis = await self._get_redis()

            # ⚠️ 使用原始JSON删除，而非序列化修改后的signal
            # signal对象可能被添加了processing_started_at等字段
            signal_json = signal.get('_original_json')
            if signal_json is None:
                # 降级方案：如果没有原始JSON，使用序列化
                logger.warning(f"⚠️ 信号缺少_original_json，使用降级方案")
                signal_json = self._serialize_signal(signal)

            result = await redis.zrem(self.processing_key, signal_json)

            if result > 0:
                logger.debug(f"✅ 信号处理完成: {signal['symbol']}")
            else:
                logger.warning(
                    f"⚠️ 从processing队列删除失败: {signal['symbol']}, "
                    f"可能已被其他进程删除"
                )

            return True

        except Exception as e:
            logger.error(f"❌ 标记完成失败: {e}")
            return False

    async def mark_signal_failed(
        self,
        signal: Dict,
        error_message: str,
        retry: bool = True
    ) -> bool:
        """
        标记信号处理失败

        Args:
            signal: 信号数据
            error_message: 错误信息
            retry: 是否重试

        Returns:
            bool: 是否成功处理
        """
        try:
            redis = await self._get_redis()

            # ⚠️ 使用原始JSON删除
            original_json = signal.get('_original_json')
            if original_json is None:
                logger.warning(f"⚠️ 信号缺少_original_json，使用降级方案")
                original_json = self._serialize_signal(signal)

            # 从processing队列移除
            await redis.zrem(self.processing_key, original_json)

            # 增加重试计数
            retry_count = signal.get('retry_count', 0) + 1
            signal['retry_count'] = retry_count
            signal['last_error'] = error_message
            signal['failed_at'] = datetime.now().isoformat()

            if retry and retry_count < self.max_retries:
                # 重新入队（降低优先级）
                original_priority = signal.get('score', 0)
                new_priority = original_priority - (retry_count * 10)  # 每次重试降低10分

                await self.publish_signal(signal, priority=new_priority)

                logger.warning(
                    f"⚠️ 信号处理失败，将重试 ({retry_count}/{self.max_retries}): "
                    f"{signal['symbol']}, 错误: {error_message}"
                )
            else:
                # 移到失败队列
                failed_signal_json = self._serialize_signal(signal)
                current_time = time.time()
                await redis.zadd(
                    self.failed_key,
                    {failed_signal_json: current_time}
                )

                # 🔥 自动清理1小时前的失败信号（防止堆积）
                one_hour_ago = current_time - 3600
                removed_count = await redis.zremrangebyscore(
                    self.failed_key,
                    '-inf',
                    one_hour_ago
                )
                if removed_count > 0:
                    logger.debug(
                        f"🗑️ 自动清理了{removed_count}个过期失败信号（>1小时）"
                    )

                logger.error(
                    f"❌ 信号处理失败（已达最大重试次数）: "
                    f"{signal['symbol']}, 错误: {error_message}"
                )

            return True

        except Exception as e:
            logger.error(f"❌ 标记失败失败: {e}")
            return False

    async def requeue_with_delay(
        self,
        signal: Dict,
        delay_minutes: int = 30,
        priority_penalty: int = 20,
        max_delay_minutes: int = 30
    ) -> bool:
        """
        延迟重新入队（用于资金不足场景）

        Args:
            signal: 信号数据
            delay_minutes: 延迟分钟数
            priority_penalty: 优先级惩罚（降低分数避免死循环）
            max_delay_minutes: 最大延迟分钟数（默认30分钟）

        Returns:
            bool: 是否成功重新入队
        """
        try:
            redis = await self._get_redis()

            # 🔥 限制最大延迟时间（防止过长延迟）
            delay_minutes = min(delay_minutes, max_delay_minutes)

            # ⚠️ 使用原始JSON从processing队列删除
            original_json = signal.get('_original_json')
            if original_json:
                try:
                    await redis.zrem(self.processing_key, original_json)
                except:
                    pass

            # 🔥 删除主队列中该标的的旧信号（防止重复）
            symbol = signal.get('symbol')
            signal_type = signal.get('type')
            if symbol:
                # 获取主队列中的所有信号
                all_signals = await redis.zrange(self.queue_key, 0, -1)
                for sig_json in all_signals:
                    try:
                        sig = self._deserialize_signal(sig_json)
                        # 如果是同一标的且同一类型，删除
                        if sig.get('symbol') == symbol and sig.get('type') == signal_type:
                            await redis.zrem(self.queue_key, sig_json)
                            logger.debug(f"🗑️ 删除旧信号: {symbol} {signal_type}")
                    except:
                        pass

            # 设置重试时间戳
            signal['retry_after'] = time.time() + (delay_minutes * 60)

            # 降低优先级
            original_priority = signal.get('score', 0)
            new_priority = max(0, original_priority - priority_penalty)

            # 重新发布
            result = await self.publish_signal(signal, priority=new_priority)

            if result:
                logger.debug(
                    f"💤 信号延迟重新入队: {signal['symbol']}, "
                    f"{delay_minutes}分钟后重试, 优先级{original_priority}→{new_priority}"
                )

            return result

        except Exception as e:
            logger.error(f"❌ 延迟重新入队失败: {e}")
            return False

    async def get_queue_size(self) -> int:
        """获取主队列大小"""
        try:
            redis = await self._get_redis()
            return await redis.zcard(self.queue_key)
        except Exception as e:
            logger.error(f"❌ 获取队列大小失败: {e}")
            return 0

    async def get_processing_size(self) -> int:
        """获取处理中队列大小"""
        try:
            redis = await self._get_redis()
            return await redis.zcard(self.processing_key)
        except Exception as e:
            logger.error(f"❌ 获取处理中队列大小失败: {e}")
            return 0

    async def get_failed_size(self) -> int:
        """获取失败队列大小"""
        try:
            redis = await self._get_redis()
            return await redis.zcard(self.failed_key)
        except Exception as e:
            logger.error(f"❌ 获取失败队列大小失败: {e}")
            return 0

    async def get_lowest_score(self) -> float:
        """
        获取队列中最低的信号分数

        Returns:
            float: 最低分数，队列为空返回0
        """
        try:
            redis = await self._get_redis()
            # ZRANGE获取score最大的一个（因为用负数，最大=-最低）
            result = await redis.zrange(self.queue_key, -1, -1, withscores=True)
            if result:
                signal_json, score = result[0]
                return -score  # 转回正数
            return 0
        except Exception as e:
            logger.error(f"❌ 获取队列最低分数失败: {e}")
            return 0

    async def get_all_signals(self, limit: int = 100) -> List[Dict]:
        """
        获取队列中所有信号（用于监控）

        Args:
            limit: 最多返回的信号数量

        Returns:
            List[Dict]: 信号列表，按优先级排序
        """
        try:
            redis = await self._get_redis()

            # 获取所有信号（按score升序，即优先级降序）
            results = await redis.zrange(
                self.queue_key,
                0,
                limit - 1,
                withscores=True
            )

            signals = []
            for signal_json, score in results:
                signal = self._deserialize_signal(signal_json)
                signal['queue_priority'] = -score
                signals.append(signal)

            return signals

        except Exception as e:
            logger.error(f"❌ 获取所有信号失败: {e}")
            return []

    async def clear_queue(self, queue_type: str = "main") -> int:
        """
        清空队列（危险操作，仅用于测试或维护）

        Args:
            queue_type: 队列类型 ('main', 'processing', 'failed', 'all')

        Returns:
            int: 删除的信号数量
        """
        try:
            redis = await self._get_redis()
            count = 0

            if queue_type in ('main', 'all'):
                count += await redis.delete(self.queue_key)

            if queue_type in ('processing', 'all'):
                count += await redis.delete(self.processing_key)

            if queue_type in ('failed', 'all'):
                count += await redis.delete(self.failed_key)

            logger.warning(f"⚠️ 清空队列: {queue_type}, 删除 {count} 个key")
            return count

        except Exception as e:
            logger.error(f"❌ 清空队列失败: {e}")
            return 0

    async def get_stats(self) -> Dict:
        """
        获取队列统计信息

        Returns:
            Dict: 包含各种统计指标
        """
        return {
            'queue_size': await self.get_queue_size(),
            'processing_size': await self.get_processing_size(),
            'failed_size': await self.get_failed_size(),
            'timestamp': datetime.now().isoformat()
        }

    async def has_pending_signal(self, symbol: str, signal_type: str = None) -> bool:
        """
        检查队列中是否已存在该标的的待处理信号（排除延迟信号）

        延迟信号（retry_after未到）不应阻止新信号生成。
        只有真正待处理的信号才应该去重。

        Args:
            symbol: 标的代码
            signal_type: 信号类型（可选），如'BUY', 'SELL'

        Returns:
            bool: 是否存在真正待处理的信号
        """
        try:
            redis = await self._get_redis()

            # 检查主队列
            main_signals = await redis.zrange(self.queue_key, 0, -1)
            for signal_json in main_signals:
                signal = self._deserialize_signal(signal_json)
                if signal.get('symbol') == symbol:
                    if signal_type is None or signal.get('type') == signal_type:
                        # 🔥 关键修复：排除延迟信号
                        retry_after = signal.get('retry_after')
                        if retry_after and time.time() < retry_after:
                            # 这是延迟信号，不应阻止新信号生成
                            logger.debug(
                                f"  排除延迟信号: {symbol} "
                                f"(还需等待{int(retry_after - time.time())}秒)"
                            )
                            continue
                        return True

            # 检查处理中队列
            processing_signals = await redis.zrange(self.processing_key, 0, -1)
            for signal_json in processing_signals:
                signal = self._deserialize_signal(signal_json)
                if signal.get('symbol') == symbol:
                    if signal_type is None or signal.get('type') == signal_type:
                        return True

            return False

        except Exception as e:
            logger.error(f"❌ 检查待处理信号失败: {e}")
            return False

    async def get_pending_symbols(self) -> set:
        """
        获取队列中所有待处理的标的代码（用于快速去重）

        Returns:
            set: 标的代码集合
        """
        try:
            redis = await self._get_redis()
            symbols = set()

            # 主队列
            main_signals = await redis.zrange(self.queue_key, 0, -1)
            for signal_json in main_signals:
                signal = self._deserialize_signal(signal_json)
                symbols.add(signal.get('symbol'))

            # 处理中队列
            processing_signals = await redis.zrange(self.processing_key, 0, -1)
            for signal_json in processing_signals:
                signal = self._deserialize_signal(signal_json)
                symbols.add(signal.get('symbol'))

            return symbols

        except Exception as e:
            logger.error(f"❌ 获取待处理标的失败: {e}")
            return set()

    async def count_delayed_signals(self, account: Optional[str] = None) -> int:
        """
        统计队列中延迟重试的信号数量

        Args:
            account: 账号ID（可选），如果指定则只统计该账号的信号

        Returns:
            int: 延迟信号数量
        """
        try:
            redis = await self._get_redis()
            count = 0

            # 遍历主队列中的所有信号
            signals = await redis.zrange(self.queue_key, 0, -1)
            for signal_json in signals:
                signal = self._deserialize_signal(signal_json)

                # 如果指定了账号，则过滤
                if account and signal.get('account') != account:
                    continue

                # 检查是否有retry_after字段
                if 'retry_after' in signal and signal['retry_after'] > time.time():
                    count += 1

            return count

        except Exception as e:
            logger.error(f"❌ 统计延迟信号失败: {e}")
            return 0

    async def wake_up_delayed_signals(self, account: Optional[str] = None) -> int:
        """
        唤醒延迟重试的信号（移除retry_after字段）

        当资金充足时调用此方法，让延迟的信号立即可被处理

        Args:
            account: 账号ID（可选），如果指定则只唤醒该账号的信号

        Returns:
            int: 被唤醒的信号数量
        """
        try:
            redis = await self._get_redis()
            woken_count = 0

            # 遍历主队列中的所有信号
            signals = await redis.zrange(self.queue_key, 0, -1, withscores=True)

            for signal_json, score in signals:
                signal = self._deserialize_signal(signal_json)

                # 如果指定了账号，则过滤
                if account and signal.get('account') != account:
                    continue

                # 检查是否有retry_after字段
                if 'retry_after' in signal:
                    # 移除retry_after字段
                    del signal['retry_after']

                    # 重新序列化并更新Redis
                    new_signal_json = self._serialize_signal(signal)

                    # 原子操作：删除旧信号，添加新信号
                    pipe = redis.pipeline()
                    pipe.zrem(self.queue_key, signal_json)
                    pipe.zadd(self.queue_key, {new_signal_json: score})
                    await pipe.execute()

                    woken_count += 1
                    logger.debug(
                        f"⏰ 唤醒延迟信号: {signal.get('symbol')} "
                        f"(账号={signal.get('account', 'N/A')})"
                    )

            if woken_count > 0:
                logger.info(f"✅ 已唤醒{woken_count}个延迟信号（账号={account or '全部'}）")

            return woken_count

        except Exception as e:
            logger.error(f"❌ 唤醒延迟信号失败: {e}")
            return 0

    async def get_delayed_signals(self, account: Optional[str] = None) -> List[Dict]:
        """
        获取队列中延迟重试的信号列表

        Args:
            account: 账号ID（可选），如果指定则只获取该账号的信号

        Returns:
            List[Dict]: 延迟信号列表
        """
        try:
            redis = await self._get_redis()
            delayed_signals = []

            # 遍历主队列中的所有信号
            signals = await redis.zrange(self.queue_key, 0, -1)
            for signal_json in signals:
                signal = self._deserialize_signal(signal_json)

                # 如果指定了账号，则过滤
                if account and signal.get('account') != account:
                    continue

                # 检查是否有retry_after字段且仍在延迟中
                if 'retry_after' in signal and signal['retry_after'] > time.time():
                    delayed_signals.append(signal)

            return delayed_signals

        except Exception as e:
            logger.error(f"❌ 获取延迟信号失败: {e}")
            return []

    async def get_failed_signals(
        self,
        account: Optional[str] = None,
        min_score: int = 60,
        max_age_seconds: int = 300
    ) -> List[Dict]:
        """
        获取失败队列中因资金不足而失败的高分信号

        Args:
            account: 账号ID（可选）
            min_score: 最低分数要求
            max_age_seconds: 最大失败时长（秒），超过此时间的失败信号不再考虑

        Returns:
            List[Dict]: 符合条件的失败信号列表
        """
        try:
            redis = await self._get_redis()
            failed_signals = []

            # 遍历失败队列中的所有信号
            signals = await redis.zrange(self.failed_key, 0, -1, withscores=True)
            current_time = time.time()

            for signal_json, failed_timestamp in signals:
                signal = self._deserialize_signal(signal_json)

                # 检查失败时长
                age = current_time - failed_timestamp
                if age > max_age_seconds:
                    continue

                # 如果指定了账号，则过滤
                if account and signal.get('account') != account:
                    continue

                # 检查是否为买入信号
                if signal.get('side') != 'BUY':
                    continue

                # 检查分数
                score = signal.get('score', 0)
                if score < min_score:
                    continue

                # 添加失败时间信息
                signal['failed_at'] = failed_timestamp
                signal['failed_age'] = age
                failed_signals.append(signal)

            return failed_signals

        except Exception as e:
            logger.error(f"❌ 获取失败信号失败: {e}")
            return []

    async def recover_failed_signal(self, signal: Dict) -> bool:
        """
        从失败队列恢复信号到主队列

        Args:
            signal: 要恢复的信号

        Returns:
            bool: 是否成功恢复
        """
        try:
            redis = await self._get_redis()

            # 从失败队列中移除（使用原始JSON）
            original_json = signal.get('_original_json')
            if original_json:
                removed = await redis.zrem(self.failed_key, original_json)
                if removed == 0:
                    logger.warning(f"⚠️  信号不在失败队列中: {signal.get('symbol')}")

            # 清理失败相关字段
            signal.pop('failed_at', None)
            signal.pop('failed_age', None)
            signal.pop('error', None)
            signal.pop('_original_json', None)

            # 重置重试计数
            signal['retry_count'] = 0

            # 重新发布到主队列
            success = await self.publish_signal(signal, priority=signal.get('score', 0))

            if success:
                logger.info(
                    f"✅ 信号已从失败队列恢复: {signal.get('symbol')}, "
                    f"评分={signal.get('score')}"
                )

            return success

        except Exception as e:
            logger.error(f"❌ 恢复失败信号失败: {e}")
            return False
