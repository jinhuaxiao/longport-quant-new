#!/usr/bin/env python3
"""检查账户余额的所有可用字段"""

import asyncio
from loguru import logger
from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient


async def check_account_fields():
    """检查账户余额字段"""
    settings = get_settings()

    async with LongportTradingClient(settings) as client:
        # 获取账户余额
        balances = await client.account_balance()

        logger.info("=" * 70)
        logger.info("账户余额字段检查")
        logger.info("=" * 70)

        for balance in balances:
            logger.info(f"\n货币: {balance.currency}")
            logger.info("-" * 50)

            # 列出所有可用的属性
            for attr in dir(balance):
                if not attr.startswith('_'):
                    try:
                        value = getattr(balance, attr)
                        if not callable(value):
                            logger.info(f"  {attr:30} = {value}")
                    except:
                        pass

            # 特别检查一些重要字段
            logger.info("\n📊 重要字段解析:")
            logger.info(f"  total_cash (总现金):          {float(balance.total_cash):,.2f}")

            # 尝试获取其他可能的字段
            if hasattr(balance, 'cash_balance'):
                logger.info(f"  cash_balance (现金余额):      {float(balance.cash_balance):,.2f}")

            if hasattr(balance, 'available_cash'):
                logger.info(f"  available_cash (可用现金):    {float(balance.available_cash):,.2f}")

            if hasattr(balance, 'frozen_cash'):
                logger.info(f"  frozen_cash (冻结资金):       {float(balance.frozen_cash):,.2f}")

            if hasattr(balance, 'financing_cash'):
                logger.info(f"  financing_cash (融资金额):    {float(balance.financing_cash):,.2f}")

            if hasattr(balance, 'max_finance_amount'):
                logger.info(f"  max_finance_amount (最大融资): {float(balance.max_finance_amount):,.2f}")

            if hasattr(balance, 'net_assets'):
                logger.info(f"  net_assets (净资产):          {float(balance.net_assets):,.2f}")

            if hasattr(balance, 'init_margin'):
                logger.info(f"  init_margin (初始保证金):     {float(balance.init_margin):,.2f}")

            if hasattr(balance, 'margin_ratio'):
                logger.info(f"  margin_ratio (保证金比例):    {float(balance.margin_ratio):,.2f}%")

        # 获取账户类型信息
        logger.info("\n" + "=" * 70)
        logger.info("检查账户类型")
        logger.info("=" * 70)

        # 尝试获取账户信息
        try:
            # 获取资金账号信息（如果API支持）
            positions = await client.stock_positions()

            for channel in positions.channels:
                logger.info(f"\n渠道: {channel.account_channel}")
                logger.info(f"  账户类型: {'融资账户' if 'margin' in str(channel.account_channel).lower() else '现金账户'}")

        except Exception as e:
            logger.warning(f"无法获取账户类型信息: {e}")


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════════════╗
║                    账户余额字段检查                                     ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  此脚本将显示账户余额对象的所有可用字段                                  ║
║  帮助识别是否使用了融资账户以及如何正确获取可用资金                        ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
    """)

    asyncio.run(check_account_fields())