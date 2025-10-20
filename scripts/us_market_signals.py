#!/usr/bin/env python3
"""美股实时交易信号检测脚本"""

import asyncio
from datetime import datetime, timedelta
from loguru import logger
import pandas as pd
import numpy as np
from zoneinfo import ZoneInfo

from longport_quant.config import get_settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.data.watchlist import WatchlistLoader
from sqlalchemy import text


def is_us_trading_time():
    """检查当前是否是美股交易时间（北京时间）"""
    beijing_tz = ZoneInfo('Asia/Shanghai')
    now = datetime.now(beijing_tz)

    # 美股交易时间（北京时间）
    # 夏令时: 21:30 - 04:00 (次日)
    # 冬令时: 22:30 - 05:00 (次日)
    # 盘前: 16:30 - 21:30/22:30

    current_hour = now.hour
    current_minute = now.minute
    current_time = current_hour * 60 + current_minute

    # 盘前时间: 16:30 - 21:30
    pre_market_start = 16 * 60 + 30  # 16:30
    pre_market_end = 21 * 60 + 30    # 21:30 (夏令时)

    # 主交易时间: 21:30 - 04:00 (跨日)
    main_market_start = 21 * 60 + 30  # 21:30
    main_market_end = 4 * 60          # 04:00

    is_pre_market = pre_market_start <= current_time < pre_market_end
    is_main_market = current_time >= main_market_start or current_time < main_market_end

    return {
        'is_trading': is_pre_market or is_main_market,
        'is_pre_market': is_pre_market,
        'is_main_market': is_main_market,
        'current_time': now.strftime('%Y-%m-%d %H:%M:%S')
    }


async def get_realtime_quote(quote_client, symbol):
    """获取实时行情"""
    try:
        quotes = await quote_client.get_realtime_quote([symbol])
        if quotes:
            quote = quotes[0]
            return {
                'symbol': symbol,
                'price': float(quote.last_done),
                'prev_close': float(quote.prev_close),
                'change_pct': ((float(quote.last_done) - float(quote.prev_close)) / float(quote.prev_close) * 100) if float(quote.prev_close) > 0 else 0,
                'volume': int(quote.volume) if hasattr(quote, 'volume') else 0,
                'turnover': float(quote.turnover) if hasattr(quote, 'turnover') else 0,
                'timestamp': datetime.now()
            }
    except Exception as e:
        logger.error(f"获取 {symbol} 实时行情失败: {e}")
    return None


async def calculate_signals_with_realtime(db, quote_client, symbol):
    """结合历史数据和实时行情计算交易信号"""

    # 获取历史K线数据
    async with db.session() as session:
        result = await session.execute(
            text("""
                SELECT trade_date, open, high, low, close, volume
                FROM kline_daily
                WHERE symbol = :symbol
                ORDER BY trade_date DESC
                LIMIT 50
            """),
            {"symbol": symbol}
        )

        history = [(row.trade_date, float(row.open), float(row.high),
                   float(row.low), float(row.close), int(row.volume))
                  for row in result]

        if len(history) < 20:
            return None

    # 获取实时行情
    realtime = await get_realtime_quote(quote_client, symbol)
    if not realtime:
        return None

    # 转换为DataFrame
    df = pd.DataFrame(history, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
    df = df.sort_values('date')

    # 添加实时数据作为最新一条
    latest_row = pd.DataFrame([{
        'date': datetime.now().date(),
        'open': df.iloc[-1]['close'],  # 使用昨收作为今开
        'high': realtime['price'],
        'low': realtime['price'],
        'close': realtime['price'],
        'volume': realtime['volume']
    }])
    df = pd.concat([df, latest_row], ignore_index=True)

    # 计算技术指标
    signals = []

    # 1. MA交叉策略
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma20'] = df['close'].rolling(window=20).mean()

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    if prev['ma5'] <= prev['ma20'] and latest['ma5'] > latest['ma20']:
        signals.append({
            'strategy': 'MA交叉',
            'signal': 'BUY',
            'reason': '金叉形成'
        })
    elif prev['ma5'] >= prev['ma20'] and latest['ma5'] < latest['ma20']:
        signals.append({
            'strategy': 'MA交叉',
            'signal': 'SELL',
            'reason': '死叉形成'
        })

    # 2. RSI策略
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))

    if latest['rsi'] < 30:
        signals.append({
            'strategy': 'RSI',
            'signal': 'BUY',
            'reason': f'超卖 (RSI={latest["rsi"]:.1f})'
        })
    elif latest['rsi'] > 70:
        signals.append({
            'strategy': 'RSI',
            'signal': 'SELL',
            'reason': f'超买 (RSI={latest["rsi"]:.1f})'
        })

    # 3. 成交量突破
    avg_volume = df['volume'].iloc[-20:-1].mean()
    if latest['volume'] > avg_volume * 2:
        if realtime['change_pct'] > 1:
            signals.append({
                'strategy': '成交量',
                'signal': 'BUY',
                'reason': f'放量上涨 (量比={latest["volume"]/avg_volume:.1f})'
            })
        elif realtime['change_pct'] < -1:
            signals.append({
                'strategy': '成交量',
                'signal': 'SELL',
                'reason': f'放量下跌 (量比={latest["volume"]/avg_volume:.1f})'
            })

    if signals:
        return {
            'symbol': symbol,
            'price': realtime['price'],
            'change_pct': realtime['change_pct'],
            'signals': signals,
            'indicators': {
                'ma5': latest['ma5'],
                'ma20': latest['ma20'],
                'rsi': latest['rsi'],
                'volume_ratio': latest['volume'] / avg_volume if avg_volume > 0 else 0
            }
        }

    return None


async def main():
    """主函数"""
    settings = get_settings()
    db = DatabaseSessionManager(settings.database_dsn, auto_init=True)
    quote_client = QuoteDataClient(settings)

    # 检查交易时间
    market_status = is_us_trading_time()

    print("\n" + "=" * 80)
    print("美股实时交易信号检测")
    print("=" * 80)
    print(f"当前时间: {market_status['current_time']} (北京时间)")

    if market_status['is_pre_market']:
        print("市场状态: 🟡 盘前交易")
    elif market_status['is_main_market']:
        print("市场状态: 🟢 主交易时段")
    else:
        print("市场状态: 🔴 休市")

    # 获取美股监控列表
    watchlist = WatchlistLoader().load()
    us_symbols = list(watchlist.symbols("us"))[:20]  # 限制前20个避免API限制

    print(f"\n监控美股数量: {len(us_symbols)}")
    print("=" * 80)

    # 检测交易信号
    all_signals = []

    for symbol in us_symbols:
        logger.info(f"分析 {symbol}...")
        result = await calculate_signals_with_realtime(db, quote_client, symbol)

        if result:
            all_signals.append(result)
            print(f"\n📊 {result['symbol']}")
            print(f"   当前价: ${result['price']:.2f} ({result['change_pct']:+.2f}%)")

            for signal in result['signals']:
                emoji = "🟢" if signal['signal'] == 'BUY' else "🔴"
                print(f"   {emoji} [{signal['strategy']}] {signal['signal']} - {signal['reason']}")

            print(f"   指标: MA5={result['indicators']['ma5']:.2f}, "
                  f"MA20={result['indicators']['ma20']:.2f}, "
                  f"RSI={result['indicators']['rsi']:.1f}, "
                  f"量比={result['indicators']['volume_ratio']:.1f}")

    # 汇总
    print("\n" + "=" * 80)
    print("交易信号汇总")
    print("=" * 80)

    if all_signals:
        buy_signals = []
        sell_signals = []

        for result in all_signals:
            for signal in result['signals']:
                if signal['signal'] == 'BUY':
                    buy_signals.append(f"{result['symbol']} ({signal['strategy']})")
                else:
                    sell_signals.append(f"{result['symbol']} ({signal['strategy']})")

        print(f"🟢 买入信号 ({len(buy_signals)}): {', '.join(buy_signals[:5])}")
        if len(buy_signals) > 5:
            print(f"   ...及其他 {len(buy_signals)-5} 个")

        print(f"🔴 卖出信号 ({len(sell_signals)}): {', '.join(sell_signals[:5])}")
        if len(sell_signals) > 5:
            print(f"   ...及其他 {len(sell_signals)-5} 个")
    else:
        print("暂无交易信号")

    # 建议
    print("\n" + "=" * 80)
    print("自动化交易建议")
    print("=" * 80)

    if market_status['is_trading']:
        print("✅ 当前处于交易时段，可以执行以下操作：")
        print("   1. 启动实时监控: python scripts/run_scheduler.py --mode run --enable-trading")
        print("   2. 执行单次策略: python scripts/run_scheduler.py --mode once --task execute_strategies")
        print("   3. 持续监控信号: watch -n 60 python scripts/us_market_signals.py")
    else:
        print("⏸️ 当前非交易时段，建议：")
        print("   1. 回测策略表现: python scripts/run_backtest.py")
        print("   2. 分析历史数据: python scripts/analyze_signals.py")
        print("   3. 准备下次开盘: python scripts/sync_historical_klines.py")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())