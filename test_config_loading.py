#!/usr/bin/env python3
"""测试配置加载是否正确"""

import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent))

from longport_quant.config import get_settings

def test_config(account_id: str):
    """测试指定账号的配置加载"""
    print(f"\n{'='*80}")
    print(f"测试账号: {account_id}")
    print(f"{'='*80}")

    settings = get_settings(account_id=account_id)

    print(f"✅ Account ID: {settings.account_id}")
    print(f"✅ Signal Queue Key: {settings.signal_queue_key}")
    print(f"✅ Longport App Key: {settings.longport_app_key[:20]}...")
    print(f"✅ Longport Region: {settings.longport_region}")
    print(f"✅ Redis URL: {settings.redis_url}")
    print(f"✅ Database DSN: {settings.database_dsn[:50]}...")

    # 显示 access_token 的前后各20个字符来识别
    token = settings.longport_access_token
    if token:
        print(f"✅ Access Token (前20): {token[:20]}...")
        print(f"✅ Access Token (后20): ...{token[-20:]}")
    else:
        print(f"⚠️ Access Token: None")

    print(f"{'='*80}\n")

if __name__ == "__main__":
    # 测试默认配置
    print("\n🔍 测试 1: 不指定 account_id (应使用全局 .env)")
    test_config(None)

    # 测试 paper_001
    print("\n🔍 测试 2: account_id = paper_001")
    test_config("paper_001")

    # 测试 live_001
    print("\n🔍 测试 3: account_id = live_001")
    test_config("live_001")
