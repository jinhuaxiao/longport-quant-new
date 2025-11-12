#!/usr/bin/env python3
"""检查数据库中K线数据的完整性"""

import asyncio
import sys
from datetime import datetime, timedelta, date
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from sqlalchemy import select, func, and_
from longport_quant.config import get_settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import KlineDaily


async def check_kline_data():
    """检查K线数据完整性"""
    print("=" * 80)
    print(f"{'K线数据完整性检查':^80}")
    print("=" * 80)
    print()

    settings = get_settings()
    db = DatabaseSessionManager(
        dsn=settings.database_dsn,
        auto_init=True
    )

    async with db.session() as session:
        # 1. 基本统计
        print("📊 基本统计")
        print("-" * 80)

        # 总记录数
        stmt = select(func.count()).select_from(KlineDaily)
        result = await session.execute(stmt)
        total_records = result.scalar()
        print(f"   总记录数:     {total_records:,} 条")

        # 标的数量
        stmt = select(func.count(func.distinct(KlineDaily.symbol)))
        result = await session.execute(stmt)
        total_symbols = result.scalar()
        print(f"   标的数量:     {total_symbols} 个")

        # 日期范围
        stmt = select(
            func.min(KlineDaily.trade_date),
            func.max(KlineDaily.trade_date)
        )
        result = await session.execute(stmt)
        earliest, latest = result.one()
        if earliest and latest:
            print(f"   最早日期:     {earliest}")
            print(f"   最晚日期:     {latest}")
            print(f"   数据跨度:     {(latest - earliest).days} 天")
        else:
            print(f"   ⚠️  数据库为空！")
            return

        print()

        # 2. 监控标的数据覆盖（从配置文件或 watchlist 读取）
        print("📋 监控标的数据覆盖检查")
        print("-" * 80)

        # 常见监控标的（可以从配置读取）
        monitored_symbols = [
            'TSLA.US', 'NVDA.US', 'MSFT.US', 'GOOGL.US', 'AMZN.US',
            'AAPL.US', 'META.US', 'NFLX.US', 'AMD.US', 'INTC.US',
            '700.HK', '9988.HK', '3690.HK', '2318.HK', '1211.HK',
            '941.HK', '5.HK', '3988.HK', '386.HK', '2378.HK'
        ]

        # 查询每个标的的数据情况
        missing_symbols = []
        partial_symbols = []
        good_symbols = []

        required_days = 100  # 至少需要100天数据
        cutoff_date = date.today() - timedelta(days=required_days)

        for symbol in monitored_symbols:
            stmt = select(
                func.min(KlineDaily.trade_date),
                func.max(KlineDaily.trade_date),
                func.count()
            ).where(KlineDaily.symbol == symbol)

            result = await session.execute(stmt)
            row = result.one()
            earliest_date, latest_date, count = row

            if count == 0:
                missing_symbols.append(symbol)
                print(f"   ❌ {symbol:12s}: 无数据")
            elif earliest_date > cutoff_date or latest_date < (date.today() - timedelta(days=7)):
                partial_symbols.append({
                    'symbol': symbol,
                    'earliest': earliest_date,
                    'latest': latest_date,
                    'count': count
                })
                print(f"   ⚠️  {symbol:12s}: 数据不足 ({count}条, {earliest_date} ~ {latest_date})")
            else:
                good_symbols.append(symbol)
                days_old = (date.today() - latest_date).days
                print(f"   ✅ {symbol:12s}: 数据充足 ({count}条, 最新: {days_old}天前)")

        print()

        # 3. 汇总
        print("📈 数据质量汇总")
        print("-" * 80)
        print(f"   ✅ 数据充足:   {len(good_symbols)} / {len(monitored_symbols)} 个标的")
        print(f"   ⚠️  数据不足:   {len(partial_symbols)} 个标的")
        print(f"   ❌ 缺失数据:   {len(missing_symbols)} 个标的")

        print()

        # 4. 建议
        print("💡 建议操作")
        print("-" * 80)

        if len(missing_symbols) > 0 or len(partial_symbols) > 0:
            print("   需要同步K线数据的标的：")
            print()

            all_need_sync = missing_symbols + [item['symbol'] for item in partial_symbols]
            symbols_str = ','.join(all_need_sync)

            print(f"   python scripts/sync_historical_klines.py \\")
            print(f"       --symbols {symbols_str} \\")
            print(f"       --start-date {cutoff_date} \\")
            print(f"       --end-date {date.today()}")
            print()

            print("   或者批量同步所有监控标的：")
            print()
            print(f"   python scripts/sync_historical_klines.py \\")
            print(f"       --symbols {','.join(monitored_symbols)} \\")
            print(f"       --start-date {cutoff_date} \\")
            print(f"       --end-date {date.today()}")
        else:
            print("   ✅ 数据完整，可以直接实施混合模式！")

        print()
        print("=" * 80)
        print("检查完成！")
        print("=" * 80)

        # 返回检查结果
        return {
            'total_records': total_records,
            'total_symbols': total_symbols,
            'good_symbols': good_symbols,
            'partial_symbols': partial_symbols,
            'missing_symbols': missing_symbols,
            'data_ready': len(missing_symbols) == 0 and len(partial_symbols) == 0
        }


if __name__ == "__main__":
    try:
        result = asyncio.run(check_kline_data())

        # 退出码：0=数据完整，1=需要同步
        sys.exit(0 if result.get('data_ready') else 1)

    except KeyboardInterrupt:
        print("\n\n❌ 检查被中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 检查失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
