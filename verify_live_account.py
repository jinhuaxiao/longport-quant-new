#!/usr/bin/env python3
"""验证 live_001 运行时使用的账号配置"""

import sys
import asyncio
from pathlib import Path

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent))

from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient

async def verify_account():
    """验证账号配置"""
    print("\n" + "="*80)
    print("🔍 验证 live_001 账号配置")
    print("="*80 + "\n")

    # 加载配置
    settings = get_settings(account_id="live_001")

    print(f"✅ 配置已加载:")
    print(f"  • Account ID: {settings.account_id}")
    print(f"  • App Key: {settings.longport_app_key[:20]}...")
    print(f"  • App Key (后10): ...{settings.longport_app_key[-10:]}")
    print(f"  • Region: {settings.longport_region}")
    print(f"  • Access Token (前30): {settings.longport_access_token[:30]}...")
    print(f"  • Access Token (后30): ...{settings.longport_access_token[-30:]}")

    print(f"\n{'='*80}")
    print("🔄 连接到 Longport API 并获取账户信息...")
    print("="*80 + "\n")

    # 创建交易客户端
    client = LongportTradingClient(settings=settings)

    try:
        # 获取账户信息
        account_balance = await client.get_account()

        print("✅ 账户信息获取成功！\n")
        print(f"💰 账户余额信息:")
        for currency, balance in account_balance.items():
            print(f"\n  {currency} 账户:")
            print(f"    • 可用资金: ${balance.get('cash', 0):,.2f}")
            print(f"    • 购买力: ${balance.get('buying_power', 0):,.2f}")
            if balance.get('max_finance_amount'):
                print(f"    • 最大融资额: ${balance.get('max_finance_amount', 0):,.2f}")

        # 验证是实盘还是模拟盘
        print(f"\n{'='*80}")

        # 通过 app_key 判断
        if settings.longport_app_key.startswith("4a5ea2e3"):
            print("⚠️ 警告：使用的是 paper_001 (模拟账号) 的凭证！")
            print("   App Key: 4a5ea2e3... (模拟账号)")
            return False
        elif settings.longport_app_key.startswith("f0221ad1"):
            print("✅ 确认：使用的是 live_001 (真实账号) 的凭证！")
            print("   App Key: f0221ad1... (真实账号)")
            return True
        else:
            print(f"❓ 未知的 App Key: {settings.longport_app_key[:20]}...")
            return None

    except Exception as e:
        print(f"❌ 获取账户信息失败: {e}")
        import traceback
        print(traceback.format_exc())
        return False
    finally:
        # 清理客户端
        if hasattr(client, 'close'):
            await client.close()

if __name__ == "__main__":
    result = asyncio.run(verify_account())
    print("\n" + "="*80)
    if result:
        print("✅ 验证通过：live_001 使用了正确的真实账号配置")
    elif result is False:
        print("❌ 验证失败：live_001 使用了错误的配置")
    else:
        print("❓ 无法确定账号类型")
    print("="*80 + "\n")
