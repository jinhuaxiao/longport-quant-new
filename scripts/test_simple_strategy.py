#!/usr/bin/env python3
"""简化的策略测试脚本 - 直接生成信号"""

import asyncio
from datetime import datetime, timedelta
from loguru import logger
import pandas as pd
import numpy as np

from longport_quant.config import get_settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.data.watchlist import WatchlistLoader
from sqlalchemy import text


async def calculate_ma_signals(db, symbol, short_window=5, long_window=20):
    """计算移动平均交叉信号"""
    async with db.session() as session:
        # 获取K线数据
        result = await session.execute(
            text("""
                SELECT trade_date, close
                FROM kline_daily
                WHERE symbol = :symbol
                ORDER BY trade_date DESC
                LIMIT :limit
            """),
            {"symbol": symbol, "limit": long_window + 5}
        )

        data = [(row.trade_date, float(row.close)) for row in result]

        if len(data) < long_window:
            return None

        # 转换为DataFrame
        df = pd.DataFrame(data, columns=['date', 'close'])
        df = df.sort_values('date')  # 按时间正序

        # 计算移动平均
        df['ma_short'] = df['close'].rolling(window=short_window).mean()
        df['ma_long'] = df['close'].rolling(window=long_window).mean()

        # 最新数据
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else None

        # 生成信号
        if prev is not None:
            # 金叉 - 买入信号
            if prev['ma_short'] <= prev['ma_long'] and latest['ma_short'] > latest['ma_long']:
                return {
                    'symbol': symbol,
                    'signal': 'BUY',
                    'price': latest['close'],
                    'ma_short': latest['ma_short'],
                    'ma_long': latest['ma_long'],
                    'reason': 'Golden Cross'
                }
            # 死叉 - 卖出信号
            elif prev['ma_short'] >= prev['ma_long'] and latest['ma_short'] < latest['ma_long']:
                return {
                    'symbol': symbol,
                    'signal': 'SELL',
                    'price': latest['close'],
                    'ma_short': latest['ma_short'],
                    'ma_long': latest['ma_long'],
                    'reason': 'Death Cross'
                }

    return None


async def calculate_bollinger_signals(db, symbol, period=20, std_dev=2):
    """计算布林带信号"""
    async with db.session() as session:
        result = await session.execute(
            text("""
                SELECT trade_date, close, high, low
                FROM kline_daily
                WHERE symbol = :symbol
                ORDER BY trade_date DESC
                LIMIT :limit
            """),
            {"symbol": symbol, "limit": period + 5}
        )

        data = [(row.trade_date, float(row.close), float(row.high), float(row.low))
                for row in result]

        if len(data) < period:
            return None

        df = pd.DataFrame(data, columns=['date', 'close', 'high', 'low'])
        df = df.sort_values('date')

        # 计算布林带
        df['ma'] = df['close'].rolling(window=period).mean()
        df['std'] = df['close'].rolling(window=period).std()
        df['upper'] = df['ma'] + (df['std'] * std_dev)
        df['lower'] = df['ma'] - (df['std'] * std_dev)

        latest = df.iloc[-1]

        # 生成信号
        if latest['close'] <= latest['lower']:
            return {
                'symbol': symbol,
                'signal': 'BUY',
                'price': latest['close'],
                'lower_band': latest['lower'],
                'upper_band': latest['upper'],
                'reason': 'Touch Lower Band'
            }
        elif latest['close'] >= latest['upper']:
            return {
                'symbol': symbol,
                'signal': 'SELL',
                'price': latest['close'],
                'lower_band': latest['lower'],
                'upper_band': latest['upper'],
                'reason': 'Touch Upper Band'
            }

    return None


async def main():
    """主测试函数"""
    settings = get_settings()
    db = DatabaseSessionManager(settings.database_dsn, auto_init=True)

    # 加载监控列表
    watchlist = WatchlistLoader().load()
    test_symbols = list(watchlist.symbols())[:20]  # 测试前20个

    logger.info("=" * 60)
    logger.info("策略信号测试")
    logger.info("=" * 60)
    logger.info(f"测试 {len(test_symbols)} 个股票")

    # 测试MA交叉策略
    logger.info("\n移动平均交叉策略:")
    logger.info("-" * 40)
    ma_signals = []
    for symbol in test_symbols:
        signal = await calculate_ma_signals(db, symbol)
        if signal:
            ma_signals.append(signal)
            logger.info(f"  {signal['symbol']}: {signal['signal']} @ {signal['price']:.2f} - {signal['reason']}")

    if not ma_signals:
        logger.info("  没有生成交易信号")
    else:
        logger.info(f"  共生成 {len(ma_signals)} 个信号")

    # 测试布林带策略
    logger.info("\n布林带策略:")
    logger.info("-" * 40)
    bb_signals = []
    for symbol in test_symbols:
        signal = await calculate_bollinger_signals(db, symbol)
        if signal:
            bb_signals.append(signal)
            logger.info(f"  {signal['symbol']}: {signal['signal']} @ {signal['price']:.2f} - {signal['reason']}")

    if not bb_signals:
        logger.info("  没有生成交易信号")
    else:
        logger.info(f"  共生成 {len(bb_signals)} 个信号")

    # 统计
    logger.info("\n" + "=" * 60)
    logger.info("测试总结")
    logger.info("=" * 60)
    logger.info(f"测试股票数: {len(test_symbols)}")
    logger.info(f"MA交叉信号: {len(ma_signals)}")
    logger.info(f"布林带信号: {len(bb_signals)}")
    logger.info(f"总信号数: {len(ma_signals) + len(bb_signals)}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())