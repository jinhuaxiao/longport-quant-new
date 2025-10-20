#!/usr/bin/env python3
"""同步历史K线数据"""

import asyncio
import argparse
from datetime import date, datetime, timedelta
from loguru import logger

from longport_quant.config import get_settings
from longport_quant.data.kline_sync import KlineDataService
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.persistence.db import DatabaseSessionManager


async def sync_historical_data(
    symbols: list[str],
    years_back: int = 5,
    batch_size: int = 2
):
    """同步历史K线数据

    Args:
        symbols: 股票代码列表
        years_back: 获取多少年的历史数据
        batch_size: 每批处理的股票数量（避免API限制，建议2-3个）
    """
    settings = get_settings()

    # 初始化服务
    db = DatabaseSessionManager(settings.database_dsn, auto_init=True)
    quote_client = QuoteDataClient(settings)
    kline_service = KlineDataService(settings, db, quote_client)

    # 计算日期范围
    end_date = date.today()
    start_date = end_date - timedelta(days=365 * years_back)

    logger.info(f"准备同步 {len(symbols)} 个股票从 {start_date} 到 {end_date} 的日线数据")

    # 分批处理
    total_synced = 0
    failed_symbols = []

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        logger.info(f"处理第 {i//batch_size + 1} 批，共 {len(batch)} 个股票")

        try:
            # 同步这一批股票
            results = await kline_service.sync_daily_klines(
                symbols=batch,
                start_date=start_date,
                end_date=end_date
            )

            # 统计结果
            for symbol, count in results.items():
                if count > 0:
                    total_synced += count
                    logger.info(f"  {symbol}: 同步了 {count} 条记录")
                elif count == 0:
                    logger.info(f"  {symbol}: 已是最新")
                else:
                    failed_symbols.append(symbol)
                    logger.error(f"  {symbol}: 同步失败")

            # 延迟避免API限制（每日历史K线有限额）
            if i + batch_size < len(symbols):
                delay = 5  # 批次间延迟增加到5秒
                logger.info(f"等待{delay}秒避免API限制...")
                await asyncio.sleep(delay)

        except Exception as e:
            logger.error(f"批次处理失败: {e}")
            failed_symbols.extend(batch)

    # 关闭数据库连接
    await db.close()

    # 汇总结果
    logger.info("=" * 60)
    logger.info(f"同步完成！")
    logger.info(f"  总记录数: {total_synced}")
    logger.info(f"  成功股票: {len(symbols) - len(failed_symbols)}")
    logger.info(f"  失败股票: {len(failed_symbols)}")
    if failed_symbols:
        logger.warning(f"  失败列表: {', '.join(failed_symbols[:10])}" +
                      (f"...等 {len(failed_symbols)} 个" if len(failed_symbols) > 10 else ""))


def main():
    parser = argparse.ArgumentParser(description="同步历史K线数据")
    parser.add_argument(
        "--symbols",
        nargs="+",
        help="指定要同步的股票代码，不指定则使用watchlist"
    )
    parser.add_argument(
        "--years",
        type=int,
        default=5,
        help="同步多少年的历史数据（默认5年）"
    )
    parser.add_argument(
        "--market",
        choices=["hk", "us", "cn"],
        help="只同步特定市场的股票"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2,
        help="每批处理的股票数量（默认2，避免API限制）"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="限制同步的股票总数"
    )

    args = parser.parse_args()

    # 确定要同步的股票列表
    if args.symbols:
        symbols = args.symbols
    else:
        # 从watchlist加载
        watchlist = WatchlistLoader().load()
        if args.market:
            symbols = watchlist.symbols(args.market)
        else:
            symbols = list(watchlist.symbols())

    # 限制数量
    if args.limit:
        symbols = symbols[:args.limit]

    if not symbols:
        logger.error("没有找到要同步的股票")
        return

    logger.info(f"准备同步 {len(symbols)} 个股票的 {args.years} 年历史数据")
    logger.info(f"股票列表: {', '.join(symbols[:10])}" +
               (f"...等 {len(symbols)} 个" if len(symbols) > 10 else ""))

    # 运行同步
    asyncio.run(sync_historical_data(
        symbols=symbols,
        years_back=args.years,
        batch_size=args.batch_size
    ))


if __name__ == "__main__":
    main()