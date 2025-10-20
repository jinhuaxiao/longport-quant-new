#!/usr/bin/env python3
"""测试订单状态枚举值"""

from longport import openapi

print("可用的OrderStatus枚举值:")
print("-" * 50)

# 列出所有OrderStatus的属性
for attr in dir(openapi.OrderStatus):
    if not attr.startswith('_'):
        value = getattr(openapi.OrderStatus, attr)
        print(f"  openapi.OrderStatus.{attr}: {value}")

print("\n常用的订单状态:")
print("-" * 50)
print(f"  新订单: {openapi.OrderStatus.New}")
print(f"  已成交: {openapi.OrderStatus.Filled}")
print(f"  部分成交: {openapi.OrderStatus.PartialFilled}")
print(f"  已取消: {openapi.OrderStatus.Canceled}")
print(f"  已拒绝: {openapi.OrderStatus.Rejected}")
print(f"  已过期: {openapi.OrderStatus.Expired}")