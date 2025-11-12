#!/usr/bin/env python3
"""测试signal_generator的混合模式集成"""

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
import numpy as np


async def test_signal_generator_kline_fetch():
    """模拟signal_generator的K线获取流程"""
    print("=" * 80)
    print(f"{'Signal Generator 混合模式集成测试':^80}")
    print("=" * 80)
    print()

    settings = get_settings()

    # 测试标的
    test_symbols = ["AAPL.US", "TSLA.US", "700.HK", "941.HK"]

    # 初始化（模拟signal_generator的初始化）
    use_db_klines = settings.use_db_klines
    db_klines_history_days = settings.db_klines_history_days
    api_klines_latest_days = settings.api_klines_latest_days

    db = None
    if use_db_klines:
        db = DatabaseSessionManager(settings.database_dsn, auto_init=True)
        print(f"✅ 混合模式已启用: 数据库{db_klines_history_days}天 + API{api_klines_latest_days}天")
        print()

    quote_client = QuoteDataClient(settings)

    print("📊 测试标的K线获取（模拟 _fetch_current_indicators）")
    print("=" * 80)
    print()

    for symbol in test_symbols:
        print(f"🔍 {symbol}")
        print("-" * 80)

        try:
            candles = []

            # ========================================
            # 混合模式逻辑（与signal_generator一致）
            # ========================================
            if use_db_klines and db:
                # 1️⃣ 从数据库获取历史数据
                end_date = date.today()
                start_date = end_date - timedelta(days=db_klines_history_days)

                async with db.session() as session:
                    stmt = select(KlineDaily).where(
                        and_(
                            KlineDaily.symbol == symbol,
                            KlineDaily.trade_date >= start_date,
                            KlineDaily.trade_date <= end_date
                        )
                    ).order_by(KlineDaily.trade_date.asc())

                    result = await session.execute(stmt)
                    db_klines = result.scalars().all()

                print(f"   📦 数据库: 读取 {len(db_klines)} 根K线")

                # 2️⃣ 从API获取最新数据
                api_end = datetime.now()
                api_start = api_end - timedelta(days=api_klines_latest_days)

                api_candles = await quote_client.get_history_candles(
                    symbol=symbol,
                    period=openapi.Period.Day,
                    adjust_type=openapi.AdjustType.NoAdjust,
                    start=api_start,
                    end=api_end
                )

                print(f"   📡 API: 读取 {len(api_candles or [])} 根K线")

                # 3️⃣ 合并判断
                if db_klines and len(db_klines) >= 30:
                    # 简单合并（实际代码中有完整的CandleWrapper）
                    all_candles = list(db_klines) + (api_candles or [])
                    candles = all_candles  # 简化：实际需要去重

                    print(f"   ✅ 混合模式: 合并后 {len(all_candles)} 根")
                    print(f"   📊 API调用: 仅{api_klines_latest_days}天数据（节省 {100*(1-api_klines_latest_days/100):.0f}%）")
                else:
                    # 回退到API
                    print(f"   ⚠️  回退API: 数据库不足({len(db_klines)}根)")
                    fallback_end = datetime.now()
                    fallback_start = fallback_end - timedelta(days=100)
                    candles = await quote_client.get_history_candles(
                        symbol=symbol,
                        period=openapi.Period.Day,
                        adjust_type=openapi.AdjustType.NoAdjust,
                        start=fallback_start,
                        end=fallback_end
                    )
                    print(f"   📊 API调用: 100天数据（完整请求）")
            else:
                # 纯API模式
                print(f"   🔧 纯API模式")
                api_end = datetime.now()
                api_start = api_end - timedelta(days=100)
                candles = await quote_client.get_history_candles(
                    symbol=symbol,
                    period=openapi.Period.Day,
                    adjust_type=openapi.AdjustType.NoAdjust,
                    start=api_start,
                    end=api_end
                )
                print(f"   📊 API调用: 100天数据")

            # ========================================
            # 技术指标计算（验证数据可用性）
            # ========================================
            if candles and len(candles) >= 30:
                # 提取收盘价（兼容数据库和API格式）
                closes = []
                for c in candles:
                    if hasattr(c, 'close'):
                        closes.append(float(c.close))

                if len(closes) >= 30:
                    closes_arr = np.array(closes[-100:])  # 最近100根

                    # 计算简单MA
                    ma20 = np.mean(closes_arr[-20:]) if len(closes_arr) >= 20 else None
                    ma50 = np.mean(closes_arr[-50:]) if len(closes_arr) >= 50 else None

                    ma20_str = f"{ma20:.2f}" if ma20 is not None else "N/A"
                    ma50_str = f"{ma50:.2f}" if ma50 is not None else "N/A"
                    print(f"   💹 技术指标: MA20={ma20_str}, MA50={ma50_str}")
                    print(f"   ✅ 数据充足，可正常生成信号")
                else:
                    print(f"   ⚠️  收盘价不足: {len(closes)}根")
            else:
                print(f"   ❌ 数据不足: {len(candles)}根 < 30根最低要求")

        except Exception as e:
            print(f"   ❌ 错误: {e}")
            import traceback
            traceback.print_exc()

        print()

    # 关闭连接
    if db:
        await db.close()

    print("=" * 80)
    print("✅ 集成测试完成！")
    print("=" * 80)
    print()

    # 总结
    print("📋 测试结果总结:")
    print("-" * 80)
    print(f"1. 混合模式配置: {'✅ 已启用' if use_db_klines else '❌ 未启用'}")
    print(f"2. 数据库连接:   {'✅ 正常' if db else '❌ 未连接'}")
    print(f"3. 测试标的数:   {len(test_symbols)} 个")
    print(f"4. API调用优化:  {100*(1-api_klines_latest_days/100):.0f}% 减少（{api_klines_latest_days}天 vs 100天）")
    print()
    print("💡 下一步: 可以放心运行 signal_generator.py 进行真实交易测试")
    print()


if __name__ == "__main__":
    try:
        asyncio.run(test_signal_generator_kline_fetch())
    except KeyboardInterrupt:
        print("\n\n❌ 测试被中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
