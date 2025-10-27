#!/usr/bin/env python3
"""测试账号配置加载"""

import sys
sys.path.insert(0, '/data/web/longport-quant-new/src')

from longport_quant.config.settings import get_settings

def test_account_config(account_id: str | None = None):
    """测试账号配置加载"""
    print(f"\n{'='*60}")
    print(f"测试账号配置: {account_id or 'default (.env)'}")
    print(f"{'='*60}")

    try:
        settings = get_settings(account_id=account_id)

        print(f"\n✅ 配置加载成功!")
        print(f"\n账号信息:")
        print(f"  • Account ID: {settings.account_id}")
        print(f"  • APP KEY: {settings.longport_credentials.app_key[:20]}...")
        print(f"  • Region: {settings.longport_credentials.region}")

        print(f"\n队列配置:")
        print(f"  • Signal Queue: {settings.signal_queue_key}")
        print(f"  • Processing Queue: {settings.signal_processing_key}")
        print(f"  • Failed Queue: {settings.signal_failed_key}")

        print(f"\n✅ 测试通过!")
        return True

    except Exception as e:
        print(f"\n❌ 配置加载失败: {e}")
        import traceback
        print(traceback.format_exc())
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="测试账号配置加载")
    parser.add_argument("--account-id", type=str, default=None, help="账号ID")
    args = parser.parse_args()

    success = test_account_config(args.account_id)
    sys.exit(0 if success else 1)
