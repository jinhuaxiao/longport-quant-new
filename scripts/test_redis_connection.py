#!/usr/bin/env python3
"""测试Redis连接"""

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings


async def test_sync_redis():
    """测试同步Redis连接"""
    print("\n1. 测试同步Redis连接...")
    try:
        import redis
        r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        result = r.ping()
        print(f"✅ 同步连接成功: {result}")
        r.close()
        return True
    except Exception as e:
        print(f"❌ 同步连接失败: {e}")
        return False


async def test_async_redis():
    """测试异步Redis连接"""
    print("\n2. 测试异步Redis连接...")
    try:
        import redis.asyncio as aioredis

        # 使用较短的超时时间
        r = await aioredis.from_url(
            "redis://localhost:6379/0",
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,  # 5秒连接超时
            socket_timeout=5,          # 5秒操作超时
        )

        result = await r.ping()
        print(f"✅ 异步连接成功: {result}")
        await r.aclose()
        return True
    except Exception as e:
        print(f"❌ 异步连接失败: {type(e).__name__}: {e}")
        import traceback
        print(traceback.format_exc())
        return False


async def test_config_redis():
    """测试使用配置的Redis连接"""
    print("\n3. 测试使用配置的Redis URL...")
    try:
        settings = get_settings()
        print(f"   Redis URL: {settings.redis_url}")

        import redis.asyncio as aioredis
        r = await aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )

        result = await r.ping()
        print(f"✅ 配置连接成功: {result}")
        await r.aclose()
        return True
    except Exception as e:
        print(f"❌ 配置连接失败: {type(e).__name__}: {e}")
        return False


async def main():
    print("=" * 70)
    print("Redis 连接诊断")
    print("=" * 70)

    # 测试1：同步连接
    sync_ok = await test_sync_redis()

    # 测试2：异步连接
    async_ok = await test_async_redis()

    # 测试3：配置连接
    config_ok = await test_config_redis()

    print("\n" + "=" * 70)
    print("诊断结果:")
    print("=" * 70)
    print(f"  同步连接: {'✅ 成功' if sync_ok else '❌ 失败'}")
    print(f"  异步连接: {'✅ 成功' if async_ok else '❌ 失败'}")
    print(f"  配置连接: {'✅ 成功' if config_ok else '❌ 失败'}")

    if sync_ok and not async_ok:
        print("\n建议: 异步Redis连接有问题，可能需要更新redis库或使用同步版本")
    elif all([sync_ok, async_ok, config_ok]):
        print("\n✅ 所有测试通过！Redis连接正常")
    else:
        print("\n❌ Redis连接有问题，请检查Redis服务和配置")


if __name__ == "__main__":
    asyncio.run(main())
