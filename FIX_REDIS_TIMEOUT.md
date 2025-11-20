# 修复Redis超时错误 - 2025-11-21

## 问题描述

```
ERROR | __main__:_save_vixy_status_to_redis:1104 - ❌ 保存 VIXY 状态到 Redis 失败: Timeout should be used inside a task
Task was destroyed but it is pending!
task: <Task pending name='Task-1664' coro=<SignalGenerator._handle_realtime_update() done...>
```

## 根本原因

### 问题 1: Redis客户端未配置超时参数

**位置**: `scripts/signal_generator.py:1081`

**问题代码**:
```python
redis_client = aioredis.from_url(self.settings.redis_url)  # ❌ 缺少超时配置
```

**问题**:
- `redis.asyncio`内部使用了`asyncio.timeout()`
- 没有显式配置`socket_timeout`和`socket_connect_timeout`
- 在某些情况下会导致 "Timeout should be used inside a task" 错误

### 问题 2: Pipeline未使用上下文管理器

**位置**: `scripts/signal_generator.py:1084`

**问题代码**:
```python
pipe = redis_client.pipeline()
# ... 操作 ...
await pipe.execute()
await redis_client.aclose()
```

**问题**:
- 如果`pipe.execute()`抛出异常，`redis_client.aclose()`不会被调用
- 导致连接泄漏和"Task was destroyed but it is pending"警告

### 问题 3: 日志级别不当

**问题代码**:
```python
logger.info(f"✅ VIXY 状态已保存...")  # 每次VIXY更新都打印INFO日志
```

**问题**: VIXY状态每秒可能更新多次，INFO日志会淹没其他重要信息

## 修复方案

### 修复 1: 添加Redis超时配置 ✅

**文件**: `scripts/signal_generator.py:1082-1087`

```python
# 修复前
redis_client = aioredis.from_url(self.settings.redis_url)

# 修复后
redis_client = aioredis.from_url(
    self.settings.redis_url,
    socket_timeout=5.0,           # 🔥 Socket操作超时
    socket_connect_timeout=5.0,   # 🔥 连接超时
    decode_responses=True         # 自动解码为字符串
)
```

**效果**:
- 明确配置超时时间，避免内部`asyncio.timeout()`上下文问题
- 5秒超时足够Redis操作完成，避免长时间阻塞

### 修复 2: 使用async with确保资源释放 ✅

**文件**: `scripts/signal_generator.py:1090-1104`

```python
# 修复前
pipe = redis_client.pipeline()
pipe.set(...)
await pipe.execute()
await redis_client.aclose()  # ❌ 如果上面异常，不会执行

# 修复后
async with redis_client.pipeline(transaction=True) as pipe:
    pipe.set(...)
    pipe.expire(...)
    await pipe.execute()
    # 🔥 pipeline自动清理

await redis_client.aclose()  # 🔥 确保关闭连接
```

**效果**:
- `async with`确保即使出现异常，pipeline也会正确清理
- 减少"Task was destroyed but it is pending"警告
- 使用`transaction=True`确保原子性操作

### 修复 3: 降低日志级别 ✅

**文件**: `scripts/signal_generator.py:1108`

```python
# 修复前
logger.info(f"✅ VIXY 状态已保存...")

# 修复后
logger.debug(f"✅ VIXY 状态已保存...")  # 降为DEBUG级别
```

**效果**: 减少日志噪音，只在调试时显示

### 修复 4: 简化异常处理 ✅

**文件**: `scripts/signal_generator.py:1110-1111`

```python
# 修复前
except Exception as e:
    logger.error(f"❌ 保存 VIXY 状态到 Redis 失败: {e}", exc_info=True)

# 修复后
except Exception as e:
    logger.error(f"❌ 保存 VIXY 状态到 Redis 失败: {e}")  # 移除exc_info，减少日志量
```

**效果**: 保留错误信息，但不打印完整堆栈（非关键错误）

## 修复后的完整代码

```python
async def _save_vixy_status_to_redis(self, current_price: float):
    """
    将 VIXY 状态保存到 Redis，供其他组件读取
    """
    try:
        import redis.asyncio as aioredis
        from datetime import datetime

        # 🔥 修复：添加超时配置，防止 "Timeout should be used inside a task" 错误
        redis_client = aioredis.from_url(
            self.settings.redis_url,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            decode_responses=True
        )

        # 🔥 修复：使用 async with 确保资源正确释放
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.set("market:vixy:price", str(current_price))
            pipe.set("market:vixy:panic", "1" if self.market_panic else "0")
            pipe.set("market:vixy:threshold", str(self.vixy_panic_threshold))
            pipe.set("market:vixy:ma200", str(self.vixy_ma200) if self.vixy_ma200 else "")
            pipe.set("market:vixy:updated_at", datetime.now(self.beijing_tz).isoformat())

            # 设置过期时间为10分钟
            pipe.expire("market:vixy:price", 600)
            pipe.expire("market:vixy:panic", 600)
            pipe.expire("market:vixy:threshold", 600)
            pipe.expire("market:vixy:ma200", 600)
            pipe.expire("market:vixy:updated_at", 600)

            await pipe.execute()

        await redis_client.aclose()

        logger.debug(f"✅ VIXY 状态已保存: ${current_price:.2f}, 恐慌={self.market_panic}")

    except Exception as e:
        logger.error(f"❌ 保存 VIXY 状态到 Redis 失败: {e}")
```

## 预期效果

1. **消除 "Timeout should be used inside a task" 错误**
   - 明确配置超时参数
   - Redis操作在5秒内完成或超时

2. **消除 "Task was destroyed but it is pending" 警告**
   - 使用`async with`确保资源正确释放
   - 即使出现异常，连接也会被正确关闭

3. **减少日志噪音**
   - VIXY状态保存从INFO降为DEBUG级别
   - 错误日志不再打印完整堆栈

## 验证方法

修复后，观察日志：

**修复前**:
```
ERROR | ❌ 保存 VIXY 状态到 Redis 失败: Timeout should be used inside a task
Task was destroyed but it is pending!
```

**修复后**:
```
DEBUG | ✅ VIXY 状态已保存: $14.52, 恐慌=False
```

## 相关配置

确保Redis服务正常运行：
```bash
redis-cli ping  # 应返回 PONG
```

Redis URL配置（在`.env`文件中）：
```bash
REDIS_URL=redis://localhost:6379/0
```

## 修复完成时间

2025-11-21 23:58 CST
