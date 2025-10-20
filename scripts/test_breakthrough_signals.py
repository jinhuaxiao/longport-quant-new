#!/usr/bin/env python3
"""测试突破买入信号识别"""

import asyncio
from datetime import datetime, timedelta
import numpy as np
from loguru import logger
from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.features.technical_indicators import TechnicalIndicators


class BreakthroughSignalTester:
    """突破信号测试器"""

    def __init__(self):
        self.settings = get_settings()
        self.quote_client = QuoteDataClient(self.settings)

        # 测试标的
        self.test_symbols = {
            # 港股
            "0700.HK": "腾讯",
            "9988.HK": "阿里巴巴",
            "3690.HK": "美团",
            "1211.HK": "比亚迪",
            "0981.HK": "中芯国际",
            "2800.HK": "盈富基金",

            # 美股（如果有权限）
            # "NVDA.US": "英伟达",
            # "TSLA.US": "特斯拉",
        }

        # 突破参数
        self.breakout_lookback = 20
        self.volume_multiplier = 1.5
        self.momentum_threshold = 5  # ROC 5%

    async def test_breakthrough_signals(self):
        """测试突破信号识别"""
        logger.info("=" * 70)
        logger.info("📈 突破信号测试")
        logger.info("=" * 70)

        results = {
            "strong_breakout": [],
            "potential_breakout": [],
            "no_signal": [],
            "reversal_opportunity": []
        }

        for symbol, name in self.test_symbols.items():
            try:
                signal = await self.analyze_single_symbol(symbol, name)
                if signal:
                    if signal['breakout_score'] >= 60:
                        results['strong_breakout'].append(signal)
                    elif signal['breakout_score'] >= 40:
                        results['potential_breakout'].append(signal)
                    else:
                        results['no_signal'].append(signal)

                    if signal['reversal_score'] >= 40:
                        results['reversal_opportunity'].append(signal)

            except Exception as e:
                logger.error(f"分析 {symbol} 失败: {e}")

        # 显示结果
        await self.display_results(results)

    async def analyze_single_symbol(self, symbol, name):
        """分析单个标的"""
        logger.info(f"\n🔍 分析 {symbol} ({name})")
        logger.info("-" * 50)

        # 获取历史数据
        end_date = datetime.now()
        start_date = end_date - timedelta(days=60)

        candles = await self.quote_client.get_history_candles(
            symbol=symbol,
            period=openapi.Period.Day,
            adjust_type=openapi.AdjustType.NoAdjust,
            start=start_date,
            end=end_date
        )

        if not candles or len(candles) < 20:
            logger.warning(f"   数据不足")
            return None

        # 获取实时行情
        quotes = await self.quote_client.get_realtime_quote([symbol])
        if not quotes:
            return None

        current_price = float(quotes[0].last_done)
        current_volume = float(quotes[0].volume)

        # 准备数据
        closes = np.array([float(c.close) for c in candles])
        highs = np.array([float(c.high) for c in candles])
        lows = np.array([float(c.low) for c in candles])
        volumes = np.array([float(c.volume) for c in candles])

        # === 突破信号分析 ===
        breakout_score = 0
        breakout_reasons = []

        # 1. 价格突破
        recent_high = np.max(highs[-self.breakout_lookback:-1])
        recent_low = np.min(lows[-self.breakout_lookback:])
        price_range = recent_high - recent_low

        if current_price > recent_high:
            breakout_score += 30
            breakout_reasons.append(f"突破{self.breakout_lookback}日高点")
            logger.success(f"   ✅ 突破新高: ${current_price:.2f} > ${recent_high:.2f}")
        elif current_price > recent_high * 0.98:
            breakout_score += 15
            breakout_reasons.append(f"接近突破位")
            logger.info(f"   📊 接近突破: 距离高点 {(recent_high/current_price-1)*100:.1f}%")
        else:
            position_in_range = (current_price - recent_low) / price_range if price_range > 0 else 0.5
            logger.info(f"   📊 区间位置: {position_in_range*100:.0f}%")

        # 2. 成交量分析
        avg_volume = np.mean(volumes[-20:]) if len(volumes) >= 20 else 0
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1

        if volume_ratio >= 2.0:
            breakout_score += 25
            breakout_reasons.append(f"巨量({volume_ratio:.1f}倍)")
            logger.success(f"   ✅ 成交量突破: {volume_ratio:.1f}倍平均")
        elif volume_ratio >= self.volume_multiplier:
            breakout_score += 15
            breakout_reasons.append(f"放量({volume_ratio:.1f}倍)")
            logger.info(f"   📊 成交量放大: {volume_ratio:.1f}倍")
        else:
            logger.info(f"   📊 成交量正常: {volume_ratio:.1f}倍")

        # 3. 动量分析 (ROC)
        if len(closes) >= 10:
            roc = ((current_price - closes[-10]) / closes[-10]) * 100
            if roc > self.momentum_threshold * 2:
                breakout_score += 20
                breakout_reasons.append(f"强动量(ROC:{roc:.1f}%)")
                logger.success(f"   ✅ 强势动量: ROC = {roc:.1f}%")
            elif roc > self.momentum_threshold:
                breakout_score += 10
                breakout_reasons.append(f"正动量(ROC:{roc:.1f}%)")
                logger.info(f"   📊 正面动量: ROC = {roc:.1f}%")
            else:
                logger.info(f"   📊 动量: ROC = {roc:.1f}%")

        # 4. 趋势分析
        sma20 = np.mean(closes[-20:]) if len(closes) >= 20 else 0
        sma50 = np.mean(closes[-50:]) if len(closes) >= 50 else 0

        if sma20 > sma50 and current_price > sma20:
            breakout_score += 10
            breakout_reasons.append("上升趋势")
            logger.info(f"   ✅ 上升趋势: SMA20 > SMA50")
        elif sma20 < sma50:
            logger.warning(f"   ⚠️ 下降趋势: SMA20 < SMA50")

        # 5. RSI分析
        rsi = TechnicalIndicators.rsi(closes, period=14)
        current_rsi = rsi[-1] if len(rsi) > 0 else 50

        if 50 < current_rsi < 70:
            breakout_score += 5
            logger.info(f"   ✅ RSI健康: {current_rsi:.0f}")
        elif current_rsi >= 70:
            logger.warning(f"   ⚠️ RSI超买: {current_rsi:.0f}")
            breakout_score -= 10
        else:
            logger.info(f"   📊 RSI: {current_rsi:.0f}")

        # === 逆势信号分析（对比）===
        reversal_score = 0
        reversal_reasons = []

        # RSI超卖
        if current_rsi < 30:
            reversal_score += 30
            reversal_reasons.append(f"RSI超卖({current_rsi:.0f})")

        # 布林带
        bb = TechnicalIndicators.bollinger_bands(closes, period=20, num_std=2)
        if current_price <= bb['lower'][-1]:
            reversal_score += 25
            reversal_reasons.append("触及布林带下轨")

        # === 综合评分 ===
        logger.info(f"\n   📊 信号评分:")
        logger.info(f"      突破信号: {breakout_score}/100")
        logger.info(f"      逆势信号: {reversal_score}/100")

        if breakout_score >= 60:
            logger.success(f"   🎯 强突破信号！")
        elif breakout_score >= 40:
            logger.info(f"   📈 潜在突破信号")
        elif reversal_score >= 40:
            logger.warning(f"   🔄 逆势机会")
        else:
            logger.debug(f"   ⚪ 无明显信号")

        return {
            'symbol': symbol,
            'name': name,
            'price': current_price,
            'breakout_score': breakout_score,
            'breakout_reasons': breakout_reasons,
            'reversal_score': reversal_score,
            'reversal_reasons': reversal_reasons,
            'volume_ratio': volume_ratio,
            'roc': roc if 'roc' in locals() else 0,
            'rsi': current_rsi
        }

    async def display_results(self, results):
        """显示测试结果"""
        logger.info("\n" + "=" * 70)
        logger.info("📊 测试结果汇总")
        logger.info("=" * 70)

        # 强突破信号
        if results['strong_breakout']:
            logger.success(f"\n🎯 强突破信号 ({len(results['strong_breakout'])}个):")
            for sig in sorted(results['strong_breakout'], key=lambda x: x['breakout_score'], reverse=True):
                logger.success(
                    f"   {sig['symbol']}({sig['name']}): "
                    f"评分={sig['breakout_score']}, "
                    f"成交量={sig['volume_ratio']:.1f}x, "
                    f"ROC={sig['roc']:.1f}%"
                )
                logger.info(f"      原因: {', '.join(sig['breakout_reasons'])}")

        # 潜在突破
        if results['potential_breakout']:
            logger.info(f"\n📈 潜在突破信号 ({len(results['potential_breakout'])}个):")
            for sig in results['potential_breakout']:
                logger.info(
                    f"   {sig['symbol']}({sig['name']}): "
                    f"评分={sig['breakout_score']}"
                )

        # 逆势机会
        if results['reversal_opportunity']:
            logger.warning(f"\n🔄 逆势机会 ({len(results['reversal_opportunity'])}个):")
            for sig in results['reversal_opportunity']:
                logger.warning(
                    f"   {sig['symbol']}({sig['name']}): "
                    f"逆势评分={sig['reversal_score']}, "
                    f"RSI={sig['rsi']:.0f}"
                )

        # 统计
        total = len(self.test_symbols)
        strong = len(results['strong_breakout'])
        potential = len(results['potential_breakout'])

        logger.info(f"\n📊 信号统计:")
        logger.info(f"   总标的数: {total}")
        logger.info(f"   强突破信号: {strong} ({strong/total*100:.0f}%)")
        logger.info(f"   潜在突破: {potential} ({potential/total*100:.0f}%)")
        logger.info(f"   逆势机会: {len(results['reversal_opportunity'])}")

        # 建议
        logger.info(f"\n💡 交易建议:")
        if results['strong_breakout']:
            logger.success("   ✅ 发现强突破信号，可以考虑跟随趋势买入")
        if results['reversal_opportunity']:
            logger.info("   📊 部分股票出现超卖，可等待反转信号")
        if not results['strong_breakout'] and not results['potential_breakout']:
            logger.warning("   ⚠️ 市场缺乏明显突破信号，建议观望")


async def main():
    tester = BreakthroughSignalTester()
    await tester.test_breakthrough_signals()


if __name__ == "__main__":
    asyncio.run(main())