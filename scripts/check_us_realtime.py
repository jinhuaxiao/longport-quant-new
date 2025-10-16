#!/usr/bin/env python3
"""检查美股实时行情"""

import asyncio
from datetime import datetime
from loguru import logger
from zoneinfo import ZoneInfo

from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.data.watchlist import WatchlistLoader


async def main():
    settings = get_settings()
    quote_client = QuoteDataClient(settings)

    # 获取美股列表
    watchlist = WatchlistLoader().load()
    us_symbols = list(watchlist.symbols("us"))[:10]  # 测试前10个

    beijing_tz = ZoneInfo('Asia/Shanghai')
    now = datetime.now(beijing_tz)

    print("\n" + "=" * 80)
    print("美股实时行情检查")
    print("=" * 80)
    print(f"当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")
    print(f"测试股票: {', '.join(us_symbols)}")
    print("=" * 80)

    # 测试几个主要美股
    test_symbols = ["AAPL.US", "MSFT.US", "GOOGL.US", "TSLA.US", "NVDA.US"]

    for symbol in test_symbols:
        try:
            print(f"\n检查 {symbol}:")

            # 获取静态信息
            static_info = await quote_client.get_static_info([symbol])
            if static_info:
                info = static_info[0]
                print(f"  名称: {info.name_cn if hasattr(info, 'name_cn') else 'N/A'}")

            # 获取实时行情
            quotes = await quote_client.get_realtime_quote([symbol])
            if quotes and quotes[0].last_done > 0:
                quote = quotes[0]
                change_pct = ((quote.last_done - quote.prev_close) / quote.prev_close * 100) if quote.prev_close > 0 else 0

                print(f"  最新价: ${quote.last_done:.2f}")
                print(f"  昨收价: ${quote.prev_close:.2f}")
                print(f"  涨跌幅: {change_pct:+.2f}%")
                print(f"  成交量: {quote.volume:,}" if hasattr(quote, 'volume') and quote.volume else "  成交量: N/A")
                print(f"  时间戳: {quote.timestamp}" if hasattr(quote, 'timestamp') else "  时间戳: N/A")
                print(f"  ✅ 实时数据可用")
            else:
                print(f"  ❌ 无实时数据（可能是非交易时间）")

        except Exception as e:
            print(f"  ❌ 错误: {e}")

    print("\n" + "=" * 80)
    print("提示:")
    print("  • 美股盘前: 16:30-21:30 (北京时间)")
    print("  • 美股正常: 21:30-04:00 (北京时间，夏令时)")
    print("  • 如无实时数据，可能是周末或美国假期")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())