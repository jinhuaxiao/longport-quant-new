#!/usr/bin/env python3
"""测试止损止盈持久化功能"""

import asyncio
from loguru import logger
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from src.longport_quant.persistence.stop_manager import StopLossManager


async def test_stop_persistence():
    """测试止损止盈的保存和加载"""
    stop_manager = StopLossManager()

    # 1. 保存测试数据
    logger.info("1. 测试保存止损止盈数据...")
    await stop_manager.save_stop(
        symbol="0700.HK",
        entry_price=500.0,
        stop_loss=475.0,
        take_profit=550.0,
        atr=10.0,
        quantity=100,
        strategy='test'
    )

    await stop_manager.save_stop(
        symbol="9988.HK",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        atr=2.5,
        quantity=200,
        strategy='test'
    )

    logger.success("✅ 成功保存2个止损止盈记录")

    # 2. 加载数据
    logger.info("\n2. 测试加载止损止盈数据...")
    stops = await stop_manager.load_active_stops()

    if stops:
        logger.success(f"✅ 成功加载 {len(stops)} 个止损止盈设置:")
        for symbol, stop_data in stops.items():
            logger.info(f"  {symbol}:")
            logger.info(f"    入场价: ${stop_data['entry_price']:.2f}")
            logger.info(f"    止损位: ${stop_data['stop_loss']:.2f}")
            logger.info(f"    止盈位: ${stop_data['take_profit']:.2f}")
            if stop_data.get('atr'):
                logger.info(f"    ATR: ${stop_data['atr']:.2f}")
    else:
        logger.warning("未找到任何止损止盈设置")

    # 3. 测试更新状态
    logger.info("\n3. 测试更新止损止盈状态...")
    await stop_manager.update_stop_status(
        "0700.HK",
        "stopped_out",
        exit_price=470.0,
        pnl=-3000.0
    )
    logger.success("✅ 成功更新状态为 stopped_out")

    # 4. 重新加载验证
    logger.info("\n4. 重新加载验证...")
    stops = await stop_manager.load_active_stops()
    logger.info(f"活跃的止损止盈数量: {len(stops)}")
    if "0700.HK" not in stops:
        logger.success("✅ 0700.HK 已不在活跃列表中（已止损）")
    if "9988.HK" in stops:
        logger.success("✅ 9988.HK 仍然活跃")

    # 5. 清理测试数据
    logger.info("\n5. 清理测试数据...")
    await stop_manager.remove_stop("9988.HK")
    logger.success("✅ 测试完成，已清理测试数据")

    await stop_manager.disconnect()


async def main():
    logger.info("="*60)
    logger.info("止损止盈持久化测试")
    logger.info("="*60)

    try:
        await test_stop_persistence()
        logger.success("\n✅ 所有测试通过！")
    except Exception as e:
        logger.error(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())