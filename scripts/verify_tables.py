#!/usr/bin/env python3
"""Verify database tables are created."""

import asyncio
import asyncpg

async def verify_tables():
    conn = await asyncpg.connect(
        host='127.0.0.1',
        port=5432,
        user='postgres',
        password='jinhua',
        database='longport_next_new'
    )

    try:
        # Get all tables
        tables = await conn.fetch("""
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
        """)

        print("Tables created in database:")
        print("-" * 40)
        for table in tables:
            tablename = table['tablename']
            # Get row count
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {tablename}")
            print(f"  ✓ {tablename:<25} ({count} rows)")

        print("-" * 40)
        print(f"Total tables: {len(tables)}")

    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(verify_tables())