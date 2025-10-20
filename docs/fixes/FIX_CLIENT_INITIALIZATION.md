# Client Initialization Fix - 2025-10-16

## Problem

The signal generator and order executor were crashing on startup with:
```
AttributeError: 'LongportTradingClient' object has no attribute 'initialize'
AttributeError: 'LongportTradingClient' object has no attribute 'cleanup'
```

## Root Cause

Both `signal_generator.py` and `order_executor.py` were incorrectly trying to call non-existent `initialize()` and `cleanup()` methods on `LongportTradingClient`.

The `LongportTradingClient` class actually implements the **async context manager pattern** (`__aenter__` / `__aexit__`), not separate init/cleanup methods.

## Solution

### Files Fixed
1. `scripts/signal_generator.py`
2. `scripts/order_executor.py`

### Changes Made

**Before (Broken):**
```python
async def initialize_clients(self):
    if not self.trade_client:
        self.trade_client = LongportTradingClient(self.settings)
        await self.trade_client.initialize()  # ❌ Method doesn't exist

async def cleanup(self):
    if self.trade_client:
        await self.trade_client.cleanup()  # ❌ Method doesn't exist
```

**After (Fixed):**
```python
async def run(self):
    try:
        # Use async context manager pattern (correct way)
        async with QuoteDataClient(self.settings) as quote_client, \
                   LongportTradingClient(self.settings) as trade_client:

            self.quote_client = quote_client
            self.trade_client = trade_client

            # ... rest of the logic

    finally:
        await self.signal_queue.close()
```

## Verification

### Signal Generator Test Results ✅
```bash
python3 scripts/signal_generator.py
```

Output shows:
- ✅ Clients initialize successfully
- ✅ Per-symbol analysis with detailed scores:
  - RSI得分: X/30
  - 布林带得分: X/25
  - MACD得分: X/20
  - 成交量得分: X/15
  - 趋势得分: X/10
  - **综合评分: X/100**
- ✅ Signals published to Redis queue successfully
- ✅ No crashes or errors

### Example Output
```
📊 分析 3690.HK (美团)
  实时行情: 价格=$98.55, 成交量=12,599,343

  信号评分:
    RSI得分: 15/30 (偏低(38.3))
    布林带得分: 25/25 (接近下轨 + 极度收窄)
    MACD得分: 0/20 (空头或中性)
    成交量得分: 0/15 (缩量(0.2x))
    趋势得分: 0/10 (下降趋势或中性)

  📈 综合评分: 40/100
  ✅ 决策: 生成买入信号 (得分40 >= 30)
     信号类型: WEAK_BUY
     强度: 0.40
  ✅ 信号已发送到队列: WEAK_BUY, 评分=40, 优先级=40
```

## Related Fixes

This fix builds on earlier fixes:
1. Redis connection URL (fixed from `192.168.200.59` to `localhost`)
2. Priority queue ordering (fixed from `ZPOPMAX` to `ZPOPMIN`)

## Next Steps

1. ✅ Start signal_generator.py - Now working!
2. ✅ Start order_executor.py - Now working!
3. Monitor queue with `python3 scripts/queue_monitor.py`
4. View logs:
   - `tail -f logs/signal_generator.log`
   - `tail -f logs/order_executor_1.log`

---

**Status**: ✅ **RESOLVED**
**Date**: 2025-10-16
**Impact**: Signal generator now shows all requested evaluation logs and successfully generates signals
