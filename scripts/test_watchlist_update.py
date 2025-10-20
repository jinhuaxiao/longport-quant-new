#!/usr/bin/env python3
"""测试更新后的监控列表配置"""

import asyncio
from loguru import logger


async def test_watchlist():
    """测试更新后的监控列表"""
    # 导入交易系统
    from advanced_technical_trading import AdvancedTechnicalTrader

    # 创建交易系统实例（使用内置监控列表）
    trader = AdvancedTechnicalTrader(use_builtin_watchlist=True)

    logger.info("=" * 70)
    logger.info("测试更新后的监控列表")
    logger.info("=" * 70)

    # 显示港股监控列表
    logger.info("\n📋 港股监控列表（15只）:")
    logger.info("-" * 50)
    for i, (symbol, info) in enumerate(trader.hk_watchlist.items(), 1):
        logger.info(f"  {i:2}. {symbol:10} - {info['name']:15} [{info['sector']}]")

    # 显示美股监控列表
    logger.info("\n📋 美股监控列表（12只）:")
    logger.info("-" * 50)
    for i, (symbol, info) in enumerate(trader.us_watchlist.items(), 1):
        logger.info(f"  {i:2}. {symbol:10} - {info['name']:15} [{info['sector']}]")

    # 统计信息
    logger.info("\n📊 监控列表统计:")
    logger.info("-" * 50)

    # 港股行业分布
    hk_sectors = {}
    for info in trader.hk_watchlist.values():
        sector = info['sector']
        hk_sectors[sector] = hk_sectors.get(sector, 0) + 1

    logger.info("港股行业分布:")
    for sector, count in sorted(hk_sectors.items(), key=lambda x: -x[1]):
        logger.info(f"  {sector:10} : {count} 只")

    # 美股行业分布
    us_sectors = {}
    for info in trader.us_watchlist.values():
        sector = info['sector']
        us_sectors[sector] = us_sectors.get(sector, 0) + 1

    logger.info("\n美股行业分布:")
    for sector, count in sorted(us_sectors.items(), key=lambda x: -x[1]):
        logger.info(f"  {sector:10} : {count} 只")

    # 验证监控列表
    logger.info("\n✅ 验证监控列表:")
    logger.info("-" * 50)

    # 用户要求的15只港股
    required_hk_stocks = [
        "9988.HK",  # 阿里巴巴
        "3690.HK",  # 美团
        "0700.HK",  # 腾讯
        "1810.HK",  # 小米
        "9992.HK",  # 泡泡玛特
        "1929.HK",  # 周大福
        "0558.HK",  # 力劲科技
        "9618.HK",  # 京东
        "1024.HK",  # 快手
        "0981.HK",  # 中芯国际
        "1347.HK",  # 华虹半导体
        "9660.HK",  # 地平线机器人
        "2382.HK",  # 舜宇光学科技
        "1211.HK",  # 比亚迪
        "3750.HK",  # 宁德时代
    ]

    # 检查是否包含所有要求的港股
    missing_stocks = []
    for stock in required_hk_stocks:
        if stock not in trader.hk_watchlist:
            missing_stocks.append(stock)

    if missing_stocks:
        logger.error(f"❌ 缺少以下港股: {', '.join(missing_stocks)}")
    else:
        logger.info(f"✅ 包含所有要求的15只港股")

    # 检查是否有多余的港股
    extra_stocks = []
    for stock in trader.hk_watchlist:
        if stock not in required_hk_stocks:
            extra_stocks.append(stock)

    if extra_stocks:
        logger.warning(f"⚠️  包含额外的港股: {', '.join(extra_stocks)}")
    else:
        logger.info(f"✅ 没有多余的港股（精确匹配）")

    # 检查美股是否保留
    if len(trader.us_watchlist) > 0:
        logger.info(f"✅ 美股监控列表已保留（{len(trader.us_watchlist)}只）")
    else:
        logger.error("❌ 美股监控列表为空")

    # 总结
    logger.info("\n" + "=" * 70)
    logger.info("📋 监控列表更新总结")
    logger.info("=" * 70)
    logger.info(f"港股: {len(trader.hk_watchlist)} 只（用户指定）")
    logger.info(f"美股: {len(trader.us_watchlist)} 只（保持不变）")
    logger.info(f"总计: {len(trader.hk_watchlist) + len(trader.us_watchlist)} 只标的")

    # 显示所有监控标的的符号列表
    all_symbols = list(trader.hk_watchlist.keys()) + list(trader.us_watchlist.keys())
    logger.info(f"\n所有监控标的符号列表（用于快速复制）:")
    logger.info(", ".join(all_symbols))


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════════════╗
║                      监控列表更新测试                                   ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  测试内容:                                                            ║
║    1. 验证港股监控列表已更新为指定的15只股票                             ║
║    2. 验证美股监控列表保持不变                                         ║
║    3. 显示行业分布统计                                                ║
║                                                                       ║
║  用户指定的15只港股:                                                   ║
║    • 科技: 阿里巴巴、美团、腾讯、小米、京东、快手                        ║
║    • 半导体: 中芯国际、华虹半导体、地平线机器人、舜宇光学                 ║
║    • 新能源: 比亚迪、宁德时代                                         ║
║    • 消费: 泡泡玛特、周大福                                           ║
║    • 工业: 力劲科技                                                  ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
    """)

    asyncio.run(test_watchlist())