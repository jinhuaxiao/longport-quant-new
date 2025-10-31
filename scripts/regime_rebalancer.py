#!/usr/bin/env python3
"""
Regime 去杠杆调仓器：从满仓/高仓位回落到状态机建议的目标仓位/预留现金。

用法：
  python3 scripts/regime_rebalancer.py --account-id YOUR_ACCOUNT_ID
"""

import asyncio
import argparse
from loguru import logger

from longport_quant.risk.rebalancer import RegimeRebalancer


async def main(account_id: str | None = None):
    rb = RegimeRebalancer(account_id=account_id)
    regime, plan = await rb.run_once()
    if not plan:
        logger.info(f"✅ 无需减仓（Regime={regime}）")
    else:
        total_qty = sum(p.sell_qty for p in plan)
        logger.info(f"✅ 已发布 {len(plan)} 个减仓信号，共 {total_qty} 股（Regime={regime}）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Regime 去杠杆调仓器")
    parser.add_argument("--account-id", type=str, default=None, help="账号ID（如 paper_001 或 live_001）")
    args = parser.parse_args()

    asyncio.run(main(account_id=args.account_id))

