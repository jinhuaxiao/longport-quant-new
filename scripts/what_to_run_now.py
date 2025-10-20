#!/usr/bin/env python3
"""根据当前时间推荐应该执行的任务"""

from datetime import datetime
from zoneinfo import ZoneInfo


def main():
    beijing_tz = ZoneInfo('Asia/Shanghai')
    now = datetime.now(beijing_tz)
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute
    current_time = now.strftime("%Y-%m-%d %H:%M:%S")

    print("\n" + "=" * 80)
    print("量化交易系统 - 当前任务建议")
    print("=" * 80)
    print(f"当前时间: {current_time} 北京时间")
    print(f"星期: {['一', '二', '三', '四', '五', '六', '日'][weekday]}")
    print("=" * 80)

    # 判断市场状态和建议任务
    tasks = []
    scheduled_tasks = []

    # 周末
    if weekday >= 5:
        print("\n📅 状态: 周末休市")
        tasks.append("• 运行回测: python scripts/run_backtest.py")
        tasks.append("• 数据清理: python scripts/run_scheduler.py --mode once --task cleanup_old_data")
        tasks.append("• 系统维护: python scripts/validate_production.py")
    else:
        print(f"\n📅 状态: 交易日")

        # 港股时段 (9:00-16:10)
        if 9 <= hour < 16 or (hour == 16 and minute <= 10):
            if 9 <= hour < 9 or (hour == 9 and minute < 30):
                print("🇭🇰 港股: 开盘前准备")
                scheduled_tasks.append("09:00-09:30 - 开盘前准备")
            elif (9 <= hour < 12 or (hour == 9 and minute >= 30)):
                print("🇭🇰 港股: 早市交易中")
                scheduled_tasks.append("09:30-12:00 - 早市交易")
                tasks.append("• 执行策略: python scripts/run_scheduler.py --mode once --task execute_strategies")
                tasks.append("• 同步数据: python scripts/run_scheduler.py --mode once --task sync_minute_klines")
            elif 12 <= hour < 13:
                print("🇭🇰 港股: 午休")
                scheduled_tasks.append("12:00-13:00 - 午休")
            elif 13 <= hour < 16:
                print("🇭🇰 港股: 午市交易中")
                scheduled_tasks.append("13:00-16:00 - 午市交易")
                tasks.append("• 执行策略: python scripts/run_scheduler.py --mode once --task execute_strategies")
                tasks.append("• 同步数据: python scripts/run_scheduler.py --mode once --task sync_minute_klines")
            elif hour == 16 and minute <= 10:
                print("🇭🇰 港股: 收盘竞价")
                scheduled_tasks.append("16:00-16:10 - 收盘竞价")

        # 港股收盘后 (16:10-18:00)
        if (hour == 16 and minute > 10) or hour == 17:
            print("🇭🇰 港股: 收盘后数据处理")
            scheduled_tasks.append("17:30 - 同步日线数据")
            tasks.append("• 同步日线: python scripts/run_scheduler.py --mode once --task sync_daily_klines")
            tasks.append("• 生成报告: python scripts/run_scheduler.py --mode once --task generate_risk_report")
            tasks.append("• 数据验证: python scripts/run_scheduler.py --mode once --task validate_market_data")

        # 美股盘前 (16:30-21:30)
        if (hour == 16 and minute >= 30) or (17 <= hour < 21) or (hour == 21 and minute < 30):
            print("🇺🇸 美股: 盘前交易")
            scheduled_tasks.append("16:30-21:30 - 美股盘前")
            tasks.append("• 检查美股: python scripts/us_market_signals.py")
            tasks.append("• 准备美股交易: python scripts/check_us_realtime.py")

        # 美股主交易 (21:30-04:00)
        if hour >= 21 and minute >= 30:
            print("🇺🇸 美股: 主交易时段")
            scheduled_tasks.append("21:30-04:00 - 美股主交易")
            tasks.append("• 执行美股策略: python scripts/run_scheduler.py --mode once --task execute_strategies")
            tasks.append("• 监控美股: python scripts/us_market_signals.py")
        elif hour < 4:
            print("🇺🇸 美股: 主交易时段(夜间)")
            scheduled_tasks.append("21:30-04:00 - 美股主交易")
            tasks.append("• 执行美股策略: python scripts/run_scheduler.py --mode once --task execute_strategies")

        # 清晨 (4:00-9:00)
        if 4 <= hour < 9:
            print("🌅 清晨: 数据准备")
            if hour == 8 and minute >= 30:
                scheduled_tasks.append("08:30 - 投资组合对账")
                tasks.append("• 对账: python scripts/run_scheduler.py --mode once --task reconcile_portfolio")

    # 定时任务
    if scheduled_tasks:
        print("\n⏰ 今日定时任务:")
        for task in scheduled_tasks:
            print(f"   {task}")

    # 建议执行
    if tasks:
        print("\n💡 建议立即执行:")
        for task in tasks:
            print(f"   {task}")

    # 自动化选项
    print("\n🤖 自动化选项:")
    print("   • 启动全自动: python scripts/run_scheduler.py --mode run --enable-trading")
    print("   • 启动监控: python scripts/start_auto_trading.py")
    print("   • 后台运行: nohup python scripts/run_scheduler.py --mode run --enable-trading > logs/scheduler.log 2>&1 &")

    # 其他常用命令
    print("\n📊 其他常用命令:")
    print("   • 查看状态: python scripts/system_status.py")
    print("   • 任务列表: python scripts/run_scheduler.py --mode list")
    print("   • 测试策略: python scripts/test_simple_strategy.py")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()