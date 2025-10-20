#!/usr/bin/env python3
"""测试新增的长桥API接口功能"""

import asyncio
from datetime import datetime, timedelta
from loguru import logger

from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient


async def test_quote_new_features():
    """测试新增的行情接口"""
    logger.info("=" * 60)
    logger.info("测试新增的行情接口")
    logger.info("=" * 60)

    settings = get_settings()
    async with QuoteDataClient(settings) as client:
        test_symbol = "AAPL.US"

        # 1. 测试分页历史K线
        logger.info("\n1. 测试分页历史K线 (get_history_candles_by_offset)")
        try:
            candles = await client.get_history_candles_by_offset(
                symbol=test_symbol,
                period=openapi.Period.Day,
                adjust_type=openapi.AdjustType.NoAdjust,
                offset=0,
                count=10,
            )
            logger.success(f"✅ 获取到 {len(candles)} 根K线")
            if candles:
                logger.info(f"   最新K线: {candles[0].timestamp} - Close: {candles[0].close}")
        except Exception as e:
            logger.error(f"❌ 分页历史K线失败: {e}")

        # 2. 测试行情权限等级
        logger.info("\n2. 测试行情权限等级 (get_quote_level)")
        try:
            level = await client.get_quote_level()
            logger.success(f"✅ 行情权限等级: {level}")
        except Exception as e:
            logger.error(f"❌ 查询行情权限失败: {e}")

        # 3. 测试行情套餐详情
        logger.info("\n3. 测试行情套餐详情 (get_quote_package_details)")
        try:
            details = await client.get_quote_package_details()
            logger.success(f"✅ 获取到 {len(details)} 个套餐")
            for pkg in details[:3]:  # 只显示前3个
                logger.info(f"   - {pkg}")
        except Exception as e:
            logger.error(f"❌ 查询套餐详情失败: {e}")

        # 4. 测试K线订阅（注意：需要在回调中接收数据）
        logger.info("\n4. 测试K线订阅 (subscribe_candlesticks)")
        try:
            await client.subscribe_candlesticks(test_symbol, openapi.Period.Min_1)
            logger.success(f"✅ 已订阅 {test_symbol} 的1分钟K线")

            # 等待5秒接收推送（实际使用需要设置回调）
            logger.info("   等待5秒接收K线推送...")
            await asyncio.sleep(5)

            # 取消订阅
            await client.unsubscribe_candlesticks(test_symbol, openapi.Period.Min_1)
            logger.success(f"✅ 已取消订阅 {test_symbol} 的K线")
        except Exception as e:
            logger.error(f"❌ K线订阅测试失败: {e}")


async def test_trade_new_features():
    """测试新增的交易接口"""
    logger.info("\n" + "=" * 60)
    logger.info("测试新增的交易接口")
    logger.info("=" * 60)

    settings = get_settings()
    async with LongportTradingClient(settings) as client:

        # 1. 测试订单推送订阅
        logger.info("\n1. 测试订单推送订阅 (subscribe_orders)")
        try:
            await client.subscribe_orders()
            logger.success("✅ 已订阅订单推送")

            # 设置订单变更回调
            def on_order_changed(event):
                logger.info(f"📬 订单变更: {event}")

            await client.set_on_order_changed(on_order_changed)
            logger.success("✅ 已设置订单变更回调")

            logger.info("   等待5秒接收订单推送...")
            await asyncio.sleep(5)

            # 取消订阅
            await client.unsubscribe_orders()
            logger.success("✅ 已取消订单推送订阅")
        except Exception as e:
            logger.error(f"❌ 订单推送测试失败: {e}")

        # 2. 测试改单功能（需要有实际订单）
        logger.info("\n2. 测试改单功能 (replace_order)")
        logger.warning("⚠️  改单功能需要有实际订单，跳过实际执行")
        logger.info("   API已封装，使用方法：")
        logger.info("   await client.replace_order(")
        logger.info("       order_id='xxx',")
        logger.info("       quantity=200,")
        logger.info("       price=150.0")
        logger.info("   )")


async def main():
    """主测试函数"""
    logger.info("\n" + "=" * 60)
    logger.info("长桥API新增接口测试")
    logger.info("=" * 60)

    try:
        # 测试行情接口
        await test_quote_new_features()

        # 测试交易接口
        await test_trade_new_features()

        logger.info("\n" + "=" * 60)
        logger.success("✅ 所有测试完成")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"❌ 测试过程出错: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())