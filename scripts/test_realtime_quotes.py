#!/usr/bin/env python3
"""测试实时行情获取"""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from loguru import logger

from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.data.watchlist import WatchlistLoader


async def test_quotes():
    """测试行情获取"""
    settings = get_settings()
    beijing_tz = ZoneInfo('Asia/Shanghai')

    logger.info("=" * 60)
    logger.info("测试实时行情获取")
    logger.info("=" * 60)

    # 当前时间
    now = datetime.now(beijing_tz)
    logger.info(f"当前时间: {now.strftime('%Y-%m-%d %H:%M:%S %A')}")
    logger.info(f"星期: {['一', '二', '三', '四', '五', '六', '日'][now.weekday()]}")

    # 加载自选股
    watchlist = WatchlistLoader().load()
    symbols = list(watchlist.symbols())
    logger.info(f"\n监控标的: {symbols}")

    async with QuoteDataClient(settings) as quote_client:
        try:
            logger.info("\n开始获取实时行情...")
            quotes = await quote_client.get_realtime_quote(symbols)

            logger.info(f"\n✅ API返回 {len(quotes)} 个行情对象\n")

            # 详细显示每个标的的行情
            valid_count = 0
            for quote in quotes:
                try:
                    symbol = quote.symbol
                    last_done = float(quote.last_done) if quote.last_done else 0
                    prev_close = float(quote.prev_close) if quote.prev_close else 0
                    volume = quote.volume

                    if last_done > 0:
                        change_pct = (last_done - prev_close) / prev_close * 100 if prev_close > 0 else 0
                        logger.info(
                            f"✅ {symbol:12s} "
                            f"最新价: ${last_done:8.2f}  "
                            f"昨收: ${prev_close:8.2f}  "
                            f"涨跌: {change_pct:+6.2f}%  "
                            f"成交量: {volume:>12,}"
                        )
                        valid_count += 1
                    else:
                        logger.warning(
                            f"⚠️  {symbol:12s} "
                            f"最新价: $0.00 (无数据)  "
                            f"昨收: ${prev_close:8.2f}  "
                            f"成交量: {volume:>12,}"
                        )

                except Exception as e:
                    logger.error(f"❌ {quote.symbol}: 解析失败 - {e}")

            logger.info(f"\n" + "=" * 60)
            logger.info(f"统计: 有效行情 {valid_count}/{len(quotes)}")

            if valid_count == 0:
                logger.warning("""
⚠️  所有标的价格都为0

可能原因:
1. 不在交易时段（盘前/盘后/休市）
2. API权限问题
3. 标的代码错误

当前时间段:
- 港股交易: 9:30-12:00, 13:00-16:00
- 美股交易: 21:30-次日4:00 (北京时间)

建议:
1. 在交易时段重新测试
2. 检查configs/watchlist.yml中的标的代码
3. 确认API密钥有实时行情权限
""")
            else:
                logger.success(f"\n✅ 成功获取 {valid_count} 个标的的有效行情！")

        except Exception as e:
            logger.error(f"❌ 获取行情失败: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_quotes())