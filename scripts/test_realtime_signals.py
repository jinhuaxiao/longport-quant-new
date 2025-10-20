#!/usr/bin/env python3
"""测试实时信号分析"""

import asyncio
import sys
sys.path.append('/data/web/longport-quant-new')

from momentum_breakthrough_trading import EnhancedTradingStrategy

async def main():
    # 创建策略实例（模拟模式）
    strategy = EnhancedTradingStrategy(
        use_builtin_watchlist=True,
        enable_trading=False,  # 模拟模式
        enable_slack=False      # 暂时禁用Slack
    )

    # 运行策略
    await strategy.run()

if __name__ == "__main__":
    asyncio.run(main())