#!/usr/bin/env python3
"""测试策略执行脚本"""

import asyncio
import sys
from datetime import datetime, timedelta
from loguru import logger

from longport_quant.config import get_settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.data.kline_sync import KlineDataService
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.strategies.ma_crossover import MovingAverageCrossoverStrategy
from longport_quant.strategies.bollinger_bands import BollingerBandsStrategy
from longport_quant.strategies.rsi_reversal import RSIReversalStrategy
from longport_quant.strategies.volume_breakout import VolumeBreakoutStrategy
# 跳过有导入问题的策略
from sqlalchemy import text


async def get_kline_data(db, symbol, days=50):
    """获取K线数据"""
    async with db.session() as session:
        result = await session.execute(
            text("""
                SELECT trade_date as timestamp, open, high, low, close, volume
                FROM kline_daily
                WHERE symbol = :symbol
                ORDER BY trade_date DESC
                LIMIT :limit
            """),
            {"symbol": symbol, "limit": days}
        )

        klines = []
        for row in result:
            klines.append({
                "timestamp": row.timestamp,
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": int(row.volume)
            })

        # 按时间正序
        klines.reverse()
        return klines


async def test_strategies():
    """测试策略执行"""
    settings = get_settings()
    db = DatabaseSessionManager(settings.database_dsn, auto_init=True)

    # 加载监控列表
    watchlist = WatchlistLoader().load()
    symbols = list(watchlist.symbols())[:10]  # 只测试前10个股票

    logger.info(f"测试 {len(symbols)} 个股票的策略信号")

    # 初始化策略
    strategies = [
        ("MA Crossover", MovingAverageCrossoverStrategy(short_window=5, long_window=20)),
        ("Bollinger Bands", BollingerBandsStrategy()),
        ("RSI Reversal", RSIReversalStrategy()),
        ("Volume Breakout", VolumeBreakoutStrategy()),
    ]

    # 获取每个股票的K线数据
    all_klines = {}
    for symbol in symbols:
        klines = await get_kline_data(db, symbol)
        if len(klines) >= 20:  # 确保有足够数据
            all_klines[symbol] = klines
            logger.info(f"  {symbol}: 获取了 {len(klines)} 条K线数据")
        else:
            logger.warning(f"  {symbol}: 数据不足，只有 {len(klines)} 条")

    # 测试每个策略
    for strategy_name, strategy in strategies:
        logger.info(f"\n测试策略: {strategy_name}")
        logger.info("-" * 50)

        try:
            # 生成信号
            signals = await strategy.generate_signals(
                list(all_klines.keys()),
                all_klines
            )

            if signals:
                logger.info(f"  生成了 {len(signals)} 个交易信号:")
                for signal in signals[:5]:  # 只显示前5个
                    logger.info(f"    {signal.symbol}: {signal.side} "
                              f"{signal.quantity} @ {signal.price:.2f}")
            else:
                logger.info("  没有生成交易信号")

        except Exception as e:
            logger.error(f"  策略执行失败: {e}")

    # 关闭数据库连接
    await db.close()

    logger.info("\n策略测试完成！")


async def test_realtime_data():
    """测试实时数据获取"""
    settings = get_settings()
    quote_client = QuoteDataClient(settings)

    # 测试股票
    test_symbols = ["700.HK", "9988.HK", "AAPL.US"]

    logger.info("测试实时行情获取:")
    for symbol in test_symbols:
        try:
            quotes = await quote_client.get_realtime_quote([symbol])
            if quotes:
                quote = quotes[0]
                logger.info(f"  {symbol}: 最新价={quote.last_done}, "
                          f"涨跌幅={quote.prev_close_price}%")
            else:
                logger.info(f"  {symbol}: 无法获取实时行情")
        except Exception as e:
            logger.error(f"  {symbol}: 获取失败 - {e}")


async def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("策略执行测试")
    logger.info("=" * 60)

    # 测试策略信号生成
    await test_strategies()

    # 测试实时数据
    logger.info("\n" + "=" * 60)
    logger.info("实时数据测试")
    logger.info("=" * 60)
    await test_realtime_data()


if __name__ == "__main__":
    asyncio.run(main())