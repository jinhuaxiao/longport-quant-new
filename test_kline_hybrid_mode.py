#!/usr/bin/env python3
"""测试K线混合模式实现"""

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import KlineDaily
from longport import openapi
from sqlalchemy import select, and_
from datetime import datetime, timedelta, date


async def test_hybrid_mode():
    """测试混合模式的完整流程"""
    print("=" * 80)
    print(f"{'K线混合模式测试':^80}")
    print("=" * 80)
    print()

    settings = get_settings()

    # 检查配置
    print("📋 配置检查")
    print("-" * 80)
    print(f"   混合模式启用:     {settings.use_db_klines}")
    print(f"   数据库历史天数:   {settings.db_klines_history_days}")
    print(f"   API最新天数:      {settings.api_klines_latest_days}")
    print()

    # 测试标的
    test_symbol = "AAPL.US"  # 使用AAPL作为测试标的

    # 初始化客户端
    db = DatabaseSessionManager(settings.database_dsn, auto_init=True)
    quote_client = QuoteDataClient(settings)

    # ========================================
    # 测试1: 数据库查询
    # ========================================
    print("📊 测试1: 数据库K线查询")
    print("-" * 80)

    try:
        end_date = date.today()
        start_date = end_date - timedelta(days=settings.db_klines_history_days)

        async with db.session() as session:
            stmt = select(KlineDaily).where(
                and_(
                    KlineDaily.symbol == test_symbol,
                    KlineDaily.trade_date >= start_date,
                    KlineDaily.trade_date <= end_date
                )
            ).order_by(KlineDaily.trade_date.asc())

            result = await session.execute(stmt)
            db_klines = result.scalars().all()

            if db_klines:
                print(f"   ✅ 成功: 读取到 {len(db_klines)} 根K线")
                print(f"   最早日期: {db_klines[0].trade_date}")
                print(f"   最晚日期: {db_klines[-1].trade_date}")
                print(f"   最新收盘价: ${float(db_klines[-1].close):.2f}")
            else:
                print(f"   ⚠️  警告: 数据库中没有 {test_symbol} 的数据")
                print(f"   请先运行同步脚本:")
                print(f"   python scripts/sync_historical_klines.py --symbols {test_symbol}")

    except Exception as e:
        print(f"   ❌ 错误: {e}")

    print()

    # ========================================
    # 测试2: API查询最新数据
    # ========================================
    print("📡 测试2: API最新K线查询")
    print("-" * 80)

    try:
        end_date_api = datetime.now()
        start_date_api = end_date_api - timedelta(days=settings.api_klines_latest_days)

        api_candles = await quote_client.get_history_candles(
            symbol=test_symbol,
            period=openapi.Period.Day,
            adjust_type=openapi.AdjustType.NoAdjust,
            start=start_date_api,
            end=end_date_api
        )

        if api_candles:
            print(f"   ✅ 成功: 读取到 {len(api_candles)} 根K线")
            if hasattr(api_candles[-1], 'timestamp'):
                print(f"   最新时间: {api_candles[-1].timestamp}")
            print(f"   最新收盘价: ${float(api_candles[-1].close):.2f}")
        else:
            print(f"   ❌ 错误: 无法从API获取数据")

    except Exception as e:
        print(f"   ❌ 错误: {e}")

    print()

    # ========================================
    # 测试3: 数据合并逻辑
    # ========================================
    print("🔗 测试3: K线数据合并")
    print("-" * 80)

    try:
        # 模拟合并逻辑
        if db_klines and api_candles:
            # 统计重叠的日期
            db_dates = {k.trade_date for k in db_klines}
            api_dates = set()
            for candle in api_candles:
                if hasattr(candle, 'timestamp'):
                    api_dates.add(candle.timestamp.date())

            overlap = db_dates & api_dates
            total_unique = len(db_dates | api_dates)

            print(f"   数据库K线:    {len(db_klines)} 根")
            print(f"   API K线:      {len(api_candles)} 根")
            print(f"   重叠日期:     {len(overlap)} 天 (API数据将覆盖)")
            print(f"   合并后总数:   {total_unique} 根")
            print()

            if total_unique >= 30:
                print(f"   ✅ 数据充足: 可以计算技术指标 (需要≥30根)")
            else:
                print(f"   ⚠️  数据不足: {total_unique}根 < 30根最低要求")

        elif not db_klines:
            print(f"   ⚠️  数据库无数据，将回退到纯API模式")
            print(f"   建议: 先同步历史数据")
        elif not api_candles:
            print(f"   ❌ API无数据，无法获取最新行情")

    except Exception as e:
        print(f"   ❌ 错误: {e}")

    print()

    # ========================================
    # 测试4: 预期API调用减少
    # ========================================
    print("📈 测试4: API调用优化效果")
    print("-" * 80)

    if db_klines and len(db_klines) >= 30:
        api_reduction = (1 - settings.api_klines_latest_days / 100) * 100
        print(f"   ✅ 混合模式有效")
        print(f"   原API请求:     100天K线")
        print(f"   新API请求:     {settings.api_klines_latest_days}天K线")
        print(f"   数据量减少:     {api_reduction:.0f}%")
        print(f"   数据库补充:     {settings.db_klines_history_days}天历史")
        print()
        print(f"   预计每小时API调用减少:")
        print(f"   假设20个监控标的，每小时触发5次信号生成")
        print(f"   - 优化前: 20标的 × 5次 × 100天 = 10,000天数据/小时")
        print(f"   - 优化后: 20标的 × 5次 × {settings.api_klines_latest_days}天 = {20 * 5 * settings.api_klines_latest_days}天数据/小时")
        print(f"   - 节省: {api_reduction:.0f}% API调用")
    else:
        print(f"   ⚠️  混合模式不可用（数据库数据不足）")
        print(f"   将回退到纯API模式（100天请求）")

    print()

    # 关闭连接
    await db.close()

    print("=" * 80)
    print("测试完成！")
    print("=" * 80)
    print()

    # 提供下一步建议
    print("💡 下一步操作建议:")
    print("-" * 80)

    if not db_klines or len(db_klines) < 30:
        print("1️⃣  先同步历史K线数据:")
        print(f"   python scripts/sync_historical_klines.py --symbols {test_symbol}")
        print()
        print("2️⃣  同步所有监控标的:")
        print("   python scripts/sync_historical_klines.py \\")
        print("       --symbols TSLA.US,NVDA.US,MSFT.US,GOOGL.US,AMZN.US,AAPL.US,...\\")
        print("       --years 1")
        print()
    else:
        print("1️⃣  启用混合模式:")
        print("   .env 文件中已设置 USE_DB_KLINES=true")
        print()
        print("2️⃣  运行signal_generator测试:")
        print("   python scripts/signal_generator.py")
        print()
        print("3️⃣  观察日志中的混合模式标记:")
        print("   ✅ 应该看到: \"混合模式 - 数据库90根 + API3根\"")
        print("   ⚠️  如果看到: \"回退到API模式\" 说明数据库数据不足")
        print()

    print("=" * 80)


if __name__ == "__main__":
    try:
        asyncio.run(test_hybrid_mode())
    except KeyboardInterrupt:
        print("\n\n❌ 测试被中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
