#!/usr/bin/env python3
"""检查 VIXY 实时行情"""

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from longport import openapi
from longport_quant.config import get_settings


async def check_vixy():
    """检查 VIXY 行情"""
    settings = get_settings(account_id="paper_001")

    # 创建行情客户端
    config = openapi.Config(
        app_key=settings.longport_app_key,
        app_secret=settings.longport_app_secret,
        access_token=settings.longport_access_token,
    )

    client = openapi.QuoteContext(config)

    print("\n" + "="*60)
    print("📊 检查 VIXY.US 行情")
    print("="*60)

    try:
        # 获取实时行情（同步方法，不需要 await）
        quotes = client.quote(["VIXY.US"])

        if quotes:
            quote = quotes[0]
            print(f"\n✅ VIXY.US 行情数据:")
            print(f"   当前价格: ${quote.last_done}")
            print(f"   开盘价: ${quote.open}")
            print(f"   最高价: ${quote.high}")
            print(f"   最低价: ${quote.low}")
            print(f"   成交量: {quote.volume}")
            print(f"   时间戳: {quote.timestamp}")

            # 检查恐慌阈值
            threshold = settings.vixy_panic_threshold
            print(f"\n📈 恐慌指数分析:")
            print(f"   恐慌阈值: ${threshold}")

            if quote.last_done > threshold:
                print(f"   🚨 状态: 恐慌模式激活 (${quote.last_done} > ${threshold})")
                print(f"   ⚠️  建议: 暂停买入，仅允许防御标的")
            else:
                print(f"   ✅ 状态: 正常模式 (${quote.last_done} <= ${threshold})")
                print(f"   💡 建议: 正常交易")
        else:
            print("\n❌ 未获取到 VIXY.US 行情数据")

    except Exception as e:
        print(f"\n❌ 获取行情失败: {e}")
        import traceback
        traceback.print_exc()

    finally:
        print("\n" + "="*60)


if __name__ == "__main__":
    asyncio.run(check_vixy())
