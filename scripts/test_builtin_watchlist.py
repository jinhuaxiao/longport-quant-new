#!/usr/bin/env python3
"""测试内置监控列表"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from scripts.advanced_technical_trading import AdvancedTechnicalTrader
from loguru import logger


def test_builtin_watchlist():
    """测试内置监控列表"""
    logger.info("=" * 70)
    logger.info("测试内置监控列表")
    logger.info("=" * 70)

    # 测试使用内置列表
    trader = AdvancedTechnicalTrader(use_builtin_watchlist=True)

    logger.info(f"\n港股监控列表 ({len(trader.hk_watchlist)} 个):")
    for symbol, info in list(trader.hk_watchlist.items())[:10]:
        logger.info(f"  {symbol:12s} - {info['name']:15s} ({info['sector']})")
    if len(trader.hk_watchlist) > 10:
        logger.info(f"  ... 还有 {len(trader.hk_watchlist) - 10} 个标的")

    logger.info(f"\n美股监控列表 ({len(trader.us_watchlist)} 个):")
    for symbol, info in trader.us_watchlist.items():
        logger.info(f"  {symbol:12s} - {info['name']:15s} ({info['sector']})")

    logger.info(f"\nA股监控列表 ({len(trader.a_watchlist)} 个):")
    for symbol, info in trader.a_watchlist.items():
        logger.info(f"  {symbol:12s} - {info['name']:15s} ({info['sector']})")

    # 测试市场过滤
    all_symbols = list(trader.hk_watchlist.keys()) + list(trader.us_watchlist.keys())
    logger.info(f"\n总计监控标的: {len(all_symbols)} 个")

    # 测试港股过滤
    hk_symbols = trader.filter_symbols_by_market(all_symbols, ['HK'])
    logger.info(f"\n港股时间过滤后: {len(hk_symbols)} 个")
    logger.info(f"  示例: {hk_symbols[:5]}")

    # 测试美股过滤
    us_symbols = trader.filter_symbols_by_market(all_symbols, ['US'])
    logger.info(f"\n美股时间过滤后: {len(us_symbols)} 个")
    logger.info(f"  示例: {us_symbols}")

    logger.info("\n" + "=" * 70)
    logger.info("✅ 测试完成")
    logger.info("=" * 70)


if __name__ == "__main__":
    test_builtin_watchlist()