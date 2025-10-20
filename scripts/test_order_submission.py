#!/usr/bin/env python3
"""测试订单提交功能的诊断脚本"""

import asyncio
from loguru import logger
from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient


async def test_order_submission():
    """测试订单提交流程"""
    logger.info("=" * 60)
    logger.info("开始测试订单提交功能")
    logger.info("=" * 60)

    settings = get_settings()
    trade_client = LongportTradingClient(settings)

    # 测试1: 检查账户余额
    logger.info("\n📊 测试1: 获取账户余额")
    try:
        balances = await asyncio.wait_for(
            trade_client.account_balance(),
            timeout=10.0
        )
        for balance in balances:
            logger.info(f"   {balance.currency}: 现金=${balance.total_cash:,.2f}, 购买力=${balance.buy_power:,.2f}")
        logger.success("   ✅ 账户余额获取成功")
    except asyncio.TimeoutError:
        logger.error("   ❌ 账户余额获取超时")
        return
    except Exception as e:
        logger.error(f"   ❌ 账户余额获取失败: {type(e).__name__}: {e}")
        return

    # 测试2: 检查持仓
    logger.info("\n📊 测试2: 获取股票持仓")
    try:
        positions_resp = await asyncio.wait_for(
            trade_client.stock_positions(),
            timeout=10.0
        )
        position_count = sum(len(channel.positions) for channel in positions_resp.channels)
        logger.info(f"   当前持仓数: {position_count}")
        logger.success("   ✅ 持仓获取成功")
    except asyncio.TimeoutError:
        logger.error("   ❌ 持仓获取超时")
        return
    except Exception as e:
        logger.error(f"   ❌ 持仓获取失败: {type(e).__name__}: {e}")
        return

    # 测试3: 模拟订单提交（不实际下单）
    logger.info("\n📊 测试3: 测试订单提交接口（模拟）")
    test_order = {
        "symbol": "0700.HK",  # 腾讯
        "side": "BUY",
        "quantity": 100,
        "price": 400.0,
    }

    logger.info(f"   测试订单: {test_order}")
    logger.info("   ⚠️ 注意: 这将尝试提交真实订单!")
    logger.info("   ⚠️ 如果不想实际下单，请立即按 Ctrl+C 终止")

    await asyncio.sleep(3)

    logger.info("   📤 正在提交订单...")
    try:
        order_response = await asyncio.wait_for(
            trade_client.submit_order(test_order),
            timeout=10.0
        )
        order_id = order_response.get("order_id")
        logger.success(f"   ✅ 订单提交成功 (ID: {order_id})")

        # 立即取消订单（如果成功提交）
        if order_id:
            logger.info(f"   🔄 正在取消测试订单...")
            try:
                await asyncio.wait_for(
                    trade_client.cancel_order(order_id),
                    timeout=10.0
                )
                logger.success(f"   ✅ 测试订单已取消")
            except Exception as e:
                logger.warning(f"   ⚠️ 订单取消失败: {e}")

    except asyncio.TimeoutError:
        logger.error("   ❌ 订单提交超时（10秒）")
        logger.error("   💡 可能原因:")
        logger.error("      1. 网络连接问题")
        logger.error("      2. Longport API响应慢")
        logger.error("      3. 市场未开盘，订单在等待确认")
    except Exception as e:
        logger.error(f"   ❌ 订单提交失败: {type(e).__name__}: {e}")
        logger.error(f"   💡 错误详情: {e}")

    logger.info("\n" + "=" * 60)
    logger.info("测试完成")
    logger.info("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(test_order_submission())
    except KeyboardInterrupt:
        logger.info("\n⏹️ 测试被用户中断")
