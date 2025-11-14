#!/usr/bin/env python3
"""
诊断脚本: 检查港股手数(lot_size)问题
用于排查 602001 错误 (The submitted quantity does not comply with the required multiple of the lot size)
"""

import asyncio
import sys
from typing import Optional

from loguru import logger

# 配置日志
logger.remove()
logger.add(
    sys.stdout,
    format="<level>{level: <8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
    level="DEBUG"
)


async def diagnose_lot_size(symbols: list[str]) -> None:
    """
    诊断指定股票的手数信息
    
    Args:
        symbols: 股票列表 (如 ["0558.HK", "0700.HK"])
    """
    print("\n" + "="*80)
    print("港股手数(Lot Size)诊断工具")
    print("="*80)
    
    try:
        from longport_quant.config import get_settings
        from longport_quant.data.quote_client import QuoteDataClient
        from longport_quant.persistence.db import DatabaseSessionManager
        from longport_quant.persistence.models import SecurityStatic
        from sqlalchemy import select
    except ImportError as e:
        logger.error(f"导入错误: {e}")
        return
    
    settings = get_settings()
    
    # 诊断每个符号
    for symbol in symbols:
        print(f"\n{'-'*80}")
        print(f"诊断: {symbol}")
        print('-'*80)
        
        # 步骤1: 从 API 获取
        logger.info(f"[步骤1] 从 LongPort API 查询...")
        api_lot_size = await _check_api_lot_size(settings, symbol)
        
        # 步骤2: 从数据库查询
        logger.info(f"[步骤2] 从数据库查询...")
        db_lot_size = await _check_db_lot_size(settings, symbol)
        
        # 步骤3: 验证
        logger.info(f"[步骤3] 验证...")
        _validate_lot_size(symbol, api_lot_size, db_lot_size)
        
    print(f"\n{'='*80}")
    print("诊断完成")
    print('='*80 + "\n")


async def _check_api_lot_size(settings, symbol: str) -> Optional[int]:
    """从 API 查询手数"""
    try:
        from longport_quant.data.quote_client import QuoteDataClient
        
        async with QuoteDataClient(settings) as client:
            result = await client.get_static_info([symbol])
            
            if not result:
                logger.warning("API 返回空列表")
                return None
            
            info = result[0]
            print(f"\nAPI 返回信息:")
            print(f"  Symbol: {getattr(info, 'symbol', 'N/A')}")
            print(f"  Name (CN): {getattr(info, 'name_cn', 'N/A')}")
            print(f"  Name (EN): {getattr(info, 'name_en', 'N/A')}")
            
            # 尝试多个属性
            lot_size_attrs = ['board_lot', 'lot_size', 'boardLot']
            found_lot_size = None
            
            print(f"\n手数属性检查:")
            for attr in lot_size_attrs:
                val = getattr(info, attr, None)
                status = "✓" if val and val > 0 else "✗"
                print(f"  {status} {attr}: {val}")
                
                if val and val > 0 and found_lot_size is None:
                    found_lot_size = val
            
            if found_lot_size:
                logger.success(f"API 返回有效 lot_size: {found_lot_size}股/手")
                return found_lot_size
            else:
                logger.warning("API 未返回有效的 lot_size")
                return None
                
    except Exception as e:
        logger.error(f"API 查询失败: {type(e).__name__}: {e}")
        return None


async def _check_db_lot_size(settings, symbol: str) -> Optional[int]:
    """从数据库查询手数"""
    try:
        from longport_quant.persistence.db import DatabaseSessionManager
        from longport_quant.persistence.models import SecurityStatic
        from sqlalchemy import select
        
        db = DatabaseSessionManager(settings)
        
        async with db.session() as session:
            result = await session.execute(
                select(SecurityStatic).where(SecurityStatic.symbol == symbol)
            )
            security = result.scalar_one_or_none()
            
            if not security:
                logger.warning(f"数据库中无 {symbol} 的记录")
                return None
            
            print(f"\n数据库信息:")
            print(f"  Symbol: {security.symbol}")
            print(f"  Name (CN): {security.name_cn}")
            print(f"  Name (EN): {security.name_en}")
            print(f"  Exchange: {security.exchange}")
            print(f"  Currency: {security.currency}")
            print(f"  Lot Size: {security.lot_size}")
            
            if security.lot_size and security.lot_size > 0:
                logger.success(f"数据库有效 lot_size: {security.lot_size}股/手")
                return security.lot_size
            else:
                logger.warning(f"数据库 lot_size 无效或为空: {security.lot_size}")
                return None
                
    except Exception as e:
        logger.error(f"数据库查询失败: {type(e).__name__}: {e}")
        return None


def _validate_lot_size(
    symbol: str, 
    api_lot_size: Optional[int],
    db_lot_size: Optional[int]
) -> None:
    """验证获取的 lot_size"""
    
    print(f"\n汇总:")
    print(f"  API lot_size: {api_lot_size or '无效'}")
    print(f"  DB lot_size: {db_lot_size or '无效'}")
    
    # 检查是否一致
    if api_lot_size and db_lot_size:
        if api_lot_size == db_lot_size:
            logger.success(f"✓ API 和数据库 lot_size 一致: {api_lot_size}")
        else:
            logger.warning(f"⚠️ API({api_lot_size}) 和数据库({db_lot_size}) lot_size 不一致!")
    elif api_lot_size:
        logger.info(f"使用 API lot_size: {api_lot_size}")
    elif db_lot_size:
        logger.info(f"使用数据库 lot_size: {db_lot_size}")
    else:
        logger.error(f"❌ 无法获取有效的 lot_size，系统将使用默认值 100")
        logger.info(f"建议: 同步数据库 python scripts/sync_security_data.py --symbols {symbol}")
    
    # 测试数量
    if api_lot_size or db_lot_size:
        test_lot_size = api_lot_size or db_lot_size
        print(f"\n数量测试 (lot_size={test_lot_size}):")
        
        test_quantities = [7000, 10000, 2000, 5000]
        for qty in test_quantities:
            remainder = qty % test_lot_size
            valid = "✓" if remainder == 0 else "✗"
            print(f"  {valid} {qty:>5} 股: remainder={remainder:>4}, "
                  f"{qty//test_lot_size}手 × {test_lot_size}股/手")


def main():
    """主函数"""
    # 默认检查 558.HK
    symbols = ["0558.HK", "0700.HK", "1398.HK"]  # 示例
    
    if len(sys.argv) > 1:
        # 从命令行参数获取符号
        symbols = sys.argv[1:]
    
    asyncio.run(diagnose_lot_size(symbols))


if __name__ == "__main__":
    main()
