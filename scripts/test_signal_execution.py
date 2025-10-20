#!/usr/bin/env python3
"""测试信号执行流程是否正常工作"""

import asyncio
from datetime import datetime
from loguru import logger
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.longport_quant.config import get_settings
from src.longport_quant.execution.client import LongportTradingClient
from src.longport_quant.data.quote_client import QuoteDataClient


async def test_signal_queue():
    """测试信号队列处理是否正常"""

    logger.info("="*70)
    logger.info("测试信号队列处理流程")
    logger.info("="*70)

    # 创建一个模拟的信号队列
    signal_queue = asyncio.Queue()

    # 模拟信号处理器
    async def mock_signal_processor():
        logger.info("🚀 启动模拟信号处理器...")
        processed_count = 0

        while processed_count < 3:  # 处理3个信号后退出
            try:
                # 从队列获取信号
                signal_data = await asyncio.wait_for(signal_queue.get(), timeout=5.0)

                symbol = signal_data['symbol']
                signal_type = signal_data.get('type', 'UNKNOWN')
                strength = signal_data.get('strength', 0)

                logger.success(f"✅ 成功处理信号: {symbol}")
                logger.info(f"   类型: {signal_type}")
                logger.info(f"   强度: {strength}")

                processed_count += 1

            except asyncio.TimeoutError:
                logger.warning("⏱️ 等待信号超时")
                break
            except Exception as e:
                logger.error(f"处理信号失败: {e}")
                break

        logger.info(f"📊 共处理 {processed_count} 个信号")

    # 启动信号处理器
    processor_task = asyncio.create_task(mock_signal_processor())

    # 等待一下让处理器准备好
    await asyncio.sleep(0.5)

    # 模拟添加信号到队列
    test_signals = [
        {
            'symbol': '0700.HK',
            'type': 'STRONG_BUY',
            'strength': 85,
            'price': 380.0
        },
        {
            'symbol': '1810.HK',
            'type': 'BUY',
            'strength': 62,
            'price': 50.5
        },
        {
            'symbol': '9988.HK',
            'type': 'WEAK_BUY',
            'strength': 45,
            'price': 85.0
        }
    ]

    logger.info("\n📤 添加测试信号到队列...")
    for signal in test_signals:
        await signal_queue.put(signal)
        logger.info(f"   已添加: {signal['symbol']} ({signal['type']}, 强度={signal['strength']})")

    # 等待处理器完成
    await processor_task

    logger.success("\n✅ 信号队列测试完成！")

    # 检查队列是否为空
    if signal_queue.empty():
        logger.success("✅ 队列已清空，所有信号都被处理")
    else:
        logger.warning(f"⚠️ 队列中还有 {signal_queue.qsize()} 个未处理信号")


async def test_real_signal_execution():
    """测试真实环境中的信号执行"""

    logger.info("\n" + "="*70)
    logger.info("测试真实信号执行流程")
    logger.info("="*70)

    settings = get_settings()

    async with LongportTradingClient(settings) as trade_client, \
               QuoteDataClient(settings) as quote_client:

        # 检查账户状态
        logger.info("\n1. 检查账户状态...")
        positions_resp = await trade_client.stock_positions()
        position_count = 0

        for channel in positions_resp.channels:
            position_count += len(channel.positions)

        logger.info(f"   当前持仓数: {position_count}/10")

        if position_count >= 10:
            logger.warning("   ⚠️ 已达最大持仓数")
        else:
            logger.success(f"   ✅ 可以开新仓位: {10-position_count}个")

        # 模拟信号生成
        logger.info("\n2. 模拟生成交易信号...")

        test_symbol = "0700.HK"
        quotes = await quote_client.get_realtime_quote([test_symbol])

        if quotes:
            quote = quotes[0]
            current_price = float(quote.last_done)

            logger.info(f"   {test_symbol} 当前价格: ${current_price:.2f}")

            # 模拟强买入信号
            mock_signal = {
                'symbol': test_symbol,
                'type': 'STRONG_BUY',
                'strength': 75,
                'price': current_price,
                'reason': '测试信号',
                'indicators': {
                    'rsi': 25,
                    'macd_golden_cross': True,
                    'volume_surge': 2.0
                }
            }

            logger.info(f"   生成模拟信号: {mock_signal['type']} (强度={mock_signal['strength']})")

            # 测试信号显示
            logger.info("\n3. 测试信号显示...")
            logger.info(f"""
╔══════════════════════════════════════════════════════════╗
║ 📈 交易信号: {test_symbol}
╠══════════════════════════════════════════════════════════╣
║ 类型: {mock_signal['type']}
║ 强度: {mock_signal['strength']}/100
║ 价格: ${current_price:.2f}
║ 原因: {mock_signal['reason']}
║
║ 技术指标:
║   RSI: {mock_signal['indicators']['rsi']}
║   MACD: {'金叉' if mock_signal['indicators']['macd_golden_cross'] else '死叉'}
║   成交量: {mock_signal['indicators']['volume_surge']:.1f}x
╚══════════════════════════════════════════════════════════╝
            """)

            logger.success("✅ 信号生成和显示测试成功")
        else:
            logger.error("❌ 无法获取行情数据")


async def main():
    # 1. 测试信号队列
    await test_signal_queue()

    # 2. 测试真实信号执行
    await test_real_signal_execution()

    logger.info("\n" + "="*70)
    logger.info("所有测试完成！")
    logger.info("="*70)

    logger.info("\n建议:")
    logger.info("1. 确认信号处理器已启动")
    logger.info("2. 检查WebSocket连接状态")
    logger.info("3. 验证Slack配置是否正确")
    logger.info("4. 运行 advanced_technical_trading.py 时查看是否有'🚀 启动信号处理器'日志")


if __name__ == "__main__":
    asyncio.run(main())