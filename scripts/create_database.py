#!/usr/bin/env python3
"""Create database if not exists."""

import asyncio
import asyncpg
import sys

async def create_database():
    # Connect to default postgres database
    conn = await asyncpg.connect(
        host='127.0.0.1',
        port=5432,
        user='postgres',
        password='jinhua',
        database='postgres'
    )

    try:
        # Check if database exists
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = 'longport_next_new'"
        )

        if not exists:
            # Create database
            await conn.execute('CREATE DATABASE longport_next_new')
            print("Database 'longport_next_new' created successfully!")
        else:
            print("Database 'longport_next_new' already exists.")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(create_database())