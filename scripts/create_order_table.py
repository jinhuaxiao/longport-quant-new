#!/usr/bin/env python3
"""创建订单记录表"""

import asyncio
import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from loguru import logger


async def create_order_table():
    """创建订单记录表"""

    # 从环境变量获取数据库连接字符串
    db_url = os.getenv('DATABASE_DSN', 'postgresql+asyncpg://postgres:jinhua@127.0.0.1:5432/longport_next_new')

    logger.info(f"连接到数据库: {db_url.split('@')[1]}")

    # 创建数据库引擎
    engine = create_async_engine(db_url, echo=True)

    try:
        async with engine.begin() as conn:
            # 删除旧表（如果存在）
            logger.info("检查并删除旧表...")
            await conn.execute(text("DROP TABLE IF EXISTS orderrecord CASCADE"))

            # 创建新表
            logger.info("创建订单记录表...")
            create_table_sql = """
            CREATE TABLE orderrecord (
                id SERIAL PRIMARY KEY,
                order_id VARCHAR(100) UNIQUE NOT NULL,
                symbol VARCHAR(20) NOT NULL,
                side VARCHAR(10) NOT NULL,
                quantity INTEGER NOT NULL,
                price DECIMAL(10, 2) NOT NULL,
                status VARCHAR(20) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
            await conn.execute(text(create_table_sql))

            # 创建索引
            logger.info("创建索引...")

            # 订单ID索引（唯一性已在表定义中指定）
            await conn.execute(text("CREATE INDEX idx_orderrecord_symbol ON orderrecord (symbol)"))

            # 创建时间索引（用于查询今日订单）
            await conn.execute(text("CREATE INDEX idx_orderrecord_created_at ON orderrecord (created_at)"))

            # 复合索引（symbol + side + created_at）用于快速查询
            await conn.execute(text("CREATE INDEX idx_orderrecord_symbol_side_created ON orderrecord (symbol, side, created_at)"))

            # 状态索引
            await conn.execute(text("CREATE INDEX idx_orderrecord_status ON orderrecord (status)"))

            logger.info("✅ 订单记录表创建成功！")

            # 验证表结构
            result = await conn.execute(text("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_name = 'orderrecord'
                ORDER BY ordinal_position
            """))

            logger.info("\n📋 表结构:")
            rows = result.fetchall()
            for row in rows:
                col_name, data_type, nullable, default = row
                nullable_str = "NULL" if nullable == "YES" else "NOT NULL"
                default_str = f"DEFAULT {default}" if default else ""
                logger.info(f"  {col_name:15} {data_type:20} {nullable_str:10} {default_str}")

            # 验证索引
            result = await conn.execute(text("""
                SELECT indexname
                FROM pg_indexes
                WHERE tablename = 'orderrecord'
            """))

            logger.info("\n📑 索引:")
            rows = result.fetchall()
            for row in rows:
                logger.info(f"  {row[0]}")

    finally:
        await engine.dispose()
        logger.info("\n数据库连接已关闭")


async def main():
    """主函数"""
    logger.info("=" * 70)
    logger.info("创建订单记录表")
    logger.info("=" * 70)

    try:
        await create_order_table()
    except Exception as e:
        logger.error(f"创建表失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════════════╗
║                   创建订单记录表                                       ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  此脚本将创建订单持久化所需的数据库表:                                  ║
║                                                                       ║
║  表名: orderrecord                                                    ║
║  字段:                                                                ║
║    - id: 主键                                                         ║
║    - order_id: 订单ID（唯一）                                          ║
║    - symbol: 标的代码                                                 ║
║    - side: 买卖方向 (BUY/SELL)                                         ║
║    - quantity: 数量                                                   ║
║    - price: 价格                                                      ║
║    - status: 订单状态                                                 ║
║    - created_at: 创建时间                                              ║
║    - updated_at: 更新时间                                              ║
║                                                                       ║
║  注意: 如果表已存在，将会先删除再重建                                    ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
    """)

    asyncio.run(main())