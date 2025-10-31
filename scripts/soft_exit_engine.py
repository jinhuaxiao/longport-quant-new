#!/usr/bin/env python3
"""
Soft Exit Engine (Chandelier/Donchian)

Runs an event-driven exit watcher and publishes SELL signals to the queue.
"""

import asyncio
import argparse
from loguru import logger

from longport_quant.execution.soft_exit import SoftExitEngine


async def main(account_id: str | None = None):
    engine = SoftExitEngine(account_id=account_id)
    await engine.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Soft Exit Engine - publish SELL signals on soft triggers",
    )
    parser.add_argument("--account-id", type=str, default=None, help="Account ID for config (e.g., paper_001)")
    args = parser.parse_args()

    logger.info("启动 Soft Exit Engine")
    if args.account_id:
        logger.info(f"使用账号配置: {args.account_id}")
    asyncio.run(main(account_id=args.account_id))

