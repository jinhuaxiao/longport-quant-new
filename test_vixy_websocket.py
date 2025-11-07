#!/usr/bin/env python3
"""测试 VIXY.US 是否支持 WebSocket 实时订阅"""

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from src.longport_quant.data.quote_client import QuoteDataClient
from src.longport_quant.config import get_settings


async def test_vixy_websocket():
    print("=" * 70)
    print("测试 VIXY.US WebSocket 实时订阅")
    print("=" * 70)
    print()

    settings = get_settings()
    quote_client = QuoteDataClient(settings)

    # 测试 VIXY.US
    test_symbol = "VIXY.US"

    print(f"📊 测试标的: {test_symbol}")
    print()

    # 1. 测试能否获取实时报价
    print("🔍 步骤 1: 测试获取实时报价...")
    try:
        quotes = await quote_client.get_realtime_quote([test_symbol])
        if quotes:
            quote = quotes[0]
            print(f"✅ 成功获取实时报价:")
            print(f"   符号: {quote.symbol}")
            print(f"   价格: ${float(quote.last_done):.2f}")
            print(f"   成交量: {quote.volume:,}")
            print(f"   时间: {quote.timestamp}")
        else:
            print(f"❌ 无法获取 {test_symbol} 的实时报价")
            return False
    except Exception as e:
        print(f"❌ 获取实时报价失败: {e}")
        return False

    print()

    # 2. 测试 WebSocket 订阅
    print("🔍 步骤 2: 测试 WebSocket 订阅...")
    print(f"   订阅 {test_symbol} 并等待 30 秒接收推送...")
    print()

    received_updates = []

    def on_quote_update(symbol, event):
        """行情更新回调"""
        try:
            price = float(event.last_done) if event.last_done else 0
            timestamp = event.timestamp
            received_updates.append({
                'symbol': symbol,
                'price': price,
                'timestamp': timestamp
            })
            print(f"   📈 [{len(received_updates)}] {symbol}: ${price:.2f} @ {timestamp}")
        except Exception as e:
            print(f"   ⚠️  处理更新失败: {e}")

    try:
        # 导入订阅类型
        from longport import openapi

        # 设置回调
        await quote_client.set_on_quote(on_quote_update)
        print("✅ 设置回调成功")

        # 订阅（使用 QUOTE 订阅类型）
        await quote_client.subscribe([test_symbol], [openapi.SubType.Quote], is_first_push=True)
        print("✅ 订阅成功，等待推送...")
        print()

        # 等待 30 秒
        await asyncio.sleep(30)

        # 取消订阅
        await quote_client.unsubscribe([test_symbol], [openapi.SubType.Quote])
        print()
        print(f"✅ 测试完成，共接收到 {len(received_updates)} 次推送")

    except Exception as e:
        print(f"❌ WebSocket 订阅失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 70)

    if received_updates:
        print("✅ VIXY.US 支持 WebSocket 实时订阅")
        print()
        print("📊 接收到的更新摘要:")
        print(f"   总推送次数: {len(received_updates)}")
        print(f"   第一次推送: {received_updates[0]['timestamp']}")
        print(f"   最后推送: {received_updates[-1]['timestamp']}")

        prices = [u['price'] for u in received_updates]
        print(f"   价格范围: ${min(prices):.2f} - ${max(prices):.2f}")

        print()
        print("🎉 结论: VIXY.US 可以用于实时监控！")
        return True
    else:
        print("⚠️  30秒内未收到任何推送")
        print()
        print("可能原因:")
        print("  1. 当前美股市场已收盘，VIXY 没有新的报价")
        print("  2. VIXY 不支持 WebSocket 订阅（较少见）")
        print()
        print("📝 建议:")
        print("  - 在美股交易时段（21:30-05:00北京时间）重新测试")
        print("  - 或者使用定时轮询方式获取 VIXY 价格")
        return False


if __name__ == "__main__":
    result = asyncio.run(test_vixy_websocket())
    sys.exit(0 if result else 1)
