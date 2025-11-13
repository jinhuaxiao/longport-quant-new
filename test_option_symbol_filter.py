#!/usr/bin/env python3
"""测试期权标的识别和过滤逻辑"""

import re

def is_option_symbol(symbol: str) -> bool:
    """
    判断是否为期权标的

    期权标的特征：
    - 格式：SYMBOL + YYMMDD + C/P + STRIKE + .MARKET
    - 例如：GOOGL260320C300000.US, AAPL250117P150000.US

    Args:
        symbol: 标的代码

    Returns:
        bool: 是否为期权标的
    """
    # 匹配期权格式：任意字符 + 6位数字 + C/P + 数字 + .US/.HK等
    pattern = r'^[A-Z]+\d{6}[CP]\d+\.(US|HK|SH|SZ)$'
    return bool(re.match(pattern, symbol))

print("=" * 70)
print("🧪 测试期权标的识别逻辑")
print("=" * 70)

# 测试用例
test_cases = [
    # (symbol, 是否为期权, 描述)
    ("GOOGL260320C300000.US", True, "Google Call期权"),
    ("AAPL250117P150000.US", True, "Apple Put期权"),
    ("TSLA240719C250000.US", True, "Tesla Call期权"),
    ("MSOS260116C15000.US", True, "MSOS Call期权"),
    ("SPY.US", False, "标准股票"),
    ("700.HK", False, "港股"),
    ("AAPL.US", False, "苹果股票"),
    ("GOOGL.US", False, "Google股票"),
    ("CRWV.US", False, "CRWV股票"),
    ("MARA.US", False, "MARA股票"),
]

print("\n📋 测试结果：")
print(f"{'标的代码':<30} {'识别结果':<10} {'预期结果':<10} {'状态':<10} {'描述':<20}")
print("-" * 90)

success_count = 0
fail_count = 0

for symbol, expected, description in test_cases:
    result = is_option_symbol(symbol)
    status = "✅ 通过" if result == expected else "❌ 失败"

    if result == expected:
        success_count += 1
    else:
        fail_count += 1

    result_str = "期权" if result else "股票"
    expected_str = "期权" if expected else "股票"

    print(f"{symbol:<30} {result_str:<10} {expected_str:<10} {status:<10} {description:<20}")

print("\n" + "=" * 70)
print(f"📊 测试统计")
print("=" * 70)
print(f"总测试用例: {len(test_cases)}")
print(f"✅ 通过: {success_count}")
print(f"❌ 失败: {fail_count}")
print(f"通过率: {success_count/len(test_cases)*100:.1f}%")

if fail_count == 0:
    print("\n✅ 所有测试通过！")
    print("=" * 70)
else:
    print(f"\n❌ {fail_count} 个测试失败")
    print("=" * 70)
    exit(1)
