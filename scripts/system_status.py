#!/usr/bin/env python3
"""系统状态总览脚本"""

import asyncio
from datetime import datetime
from pathlib import Path
from loguru import logger
from sqlalchemy import text

from longport_quant.config import get_settings
from longport_quant.persistence.db import DatabaseSessionManager


async def show_system_status():
    """显示系统状态"""
    settings = get_settings()

    print("\n" + "=" * 60)
    print("量化交易系统状态总览")
    print("=" * 60)
    print(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 环境配置
    print("\n[环境配置]")
    print(f"  数据库: 已配置" if settings.database_dsn else "  数据库: 未配置")
    print(f"  API密钥: 已配置" if hasattr(settings, 'app_key') else "  API密钥: 已配置")
    print(f"  区域: {getattr(settings, 'longport_region', 'cn')}")

    # 数据统计
    try:
        async with DatabaseSessionManager(settings.database_dsn) as db:
            async with db.session() as session:
                # 证券数量
                result = await session.execute(text("SELECT COUNT(*) FROM security_universe"))
                securities = result.scalar()

                # 港股和美股分别统计
                hk_result = await session.execute(
                    text("SELECT COUNT(*) FROM security_universe WHERE market = 'hk'")
                )
                hk_count = hk_result.scalar()

                us_result = await session.execute(
                    text("SELECT COUNT(*) FROM security_universe WHERE market = 'us'")
                )
                us_count = us_result.scalar()

                # K线数据
                daily_result = await session.execute(text("SELECT COUNT(*) FROM kline_daily"))
                daily_count = daily_result.scalar()

                minute_result = await session.execute(text("SELECT COUNT(*) FROM kline_minute"))
                minute_count = minute_result.scalar()

                # 最新数据日期
                latest_result = await session.execute(
                    text("SELECT MAX(trade_date) FROM kline_daily")
                )
                latest_date = latest_result.scalar()

                # 持仓和信号
                positions_result = await session.execute(text("SELECT COUNT(*) FROM positions"))
                positions_count = positions_result.scalar()

                signals_result = await session.execute(text("SELECT COUNT(*) FROM trading_signals"))
                signals_count = signals_result.scalar()

                print("\n[数据统计]")
                print(f"  证券总数: {securities:,} (港股: {hk_count:,}, 美股: {us_count:,})")
                print(f"  日线数据: {daily_count:,} 条")
                print(f"  分钟数据: {minute_count:,} 条")
                if latest_date:
                    days_old = (datetime.now().date() - latest_date).days
                    print(f"  最新数据: {latest_date} ({days_old}天前)")
                print(f"  持仓记录: {positions_count:,} 条")
                print(f"  交易信号: {signals_count:,} 条")

                # 分区统计
                partitions_result = await session.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM pg_tables
                        WHERE tablename LIKE 'kline_minute_%'
                        OR tablename LIKE 'kline_daily_%'
                    """)
                )
                partition_count = partitions_result.scalar()
                print(f"  分区表数: {partition_count} 个")

    except Exception as e:
        print(f"\n[错误] 无法获取数据统计: {e}")

    # 监控列表
    watchlist_file = Path("configs/watchlist.yml")
    if watchlist_file.exists():
        from longport_quant.data.watchlist import WatchlistLoader
        try:
            loader = WatchlistLoader()
            watchlist = loader.load()
            total = len(list(watchlist.symbols()))
            print(f"\n[监控列表]")
            print(f"  配置文件: configs/watchlist.yml")
            print(f"  监控股票: {total} 个")
        except Exception as e:
            print(f"\n[监控列表] 加载失败: {e}")
    else:
        print(f"\n[监控列表] 配置文件不存在")

    # 系统文件
    print("\n[系统文件]")
    important_files = [
        (".env", "环境配置"),
        ("configs/watchlist.yml", "监控列表"),
        ("alembic.ini", "数据库迁移"),
        ("scripts/run_scheduler.py", "调度器"),
        ("scripts/init_system.py", "系统初始化"),
        ("scripts/validate_production.py", "生产验证"),
    ]

    for file_path, desc in important_files:
        exists = "✓" if Path(file_path).exists() else "✗"
        print(f"  {exists} {desc}: {file_path}")

    # 就绪状态
    print("\n[系统就绪检查]")
    ready_items = []
    not_ready_items = []

    if settings.database_dsn:
        ready_items.append("基础配置")
    else:
        not_ready_items.append("基础配置")

    if securities > 0:
        ready_items.append("证券数据")
    else:
        not_ready_items.append("证券数据")

    if daily_count > 0 or minute_count > 0:
        ready_items.append("K线数据")
    else:
        not_ready_items.append("K线数据")

    if Path("configs/watchlist.yml").exists():
        ready_items.append("监控列表")
    else:
        not_ready_items.append("监控列表")

    for item in ready_items:
        print(f"  ✓ {item}")

    for item in not_ready_items:
        print(f"  ✗ {item}")

    # 建议
    print("\n[下一步建议]")
    if not_ready_items:
        print("  请先完成以下准备工作:")
        if "基础配置" in not_ready_items:
            print("    1. 配置 .env 文件中的数据库和API密钥")
        if "证券数据" in not_ready_items:
            print("    2. 运行 python scripts/sync_security_data.py 同步证券列表")
        if "K线数据" in not_ready_items:
            print("    3. 运行 python scripts/sync_historical_klines.py 同步历史数据")
        if "监控列表" in not_ready_items:
            print("    4. 运行 python scripts/init_watchlist.py 初始化监控列表")
    else:
        print("  ✅ 系统已就绪！可以运行以下命令:")
        print("    - python scripts/validate_production.py  # 验证生产环境")
        print("    - python scripts/run_scheduler.py --mode test  # 测试调度器")
        print("    - python scripts/run_scheduler.py --mode run   # 启动生产调度器")
        print("    - python scripts/e2e_test.py  # 运行端到端测试")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    asyncio.run(show_system_status())