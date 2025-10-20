"""止损止盈持久化管理器"""

import os
import asyncpg
from datetime import datetime
from typing import Dict, List, Optional
from loguru import logger


class StopLossManager:
    """止损止盈持久化管理器"""

    def __init__(self):
        self.db_url = os.getenv('DATABASE_DSN', 'postgresql://postgres:jinhua@127.0.0.1:5432/longport_next_new')
        if self.db_url.startswith('postgresql+asyncpg://'):
            self.db_url = self.db_url.replace('postgresql+asyncpg://', 'postgresql://')
        self.pool = None

    async def connect(self):
        """连接数据库（使用连接池）"""
        if not self.pool or self.pool._closed:
            self.pool = await asyncpg.create_pool(
                self.db_url,
                min_size=1,                            # 最小连接数
                max_size=2,                            # 降低到2个最大连接
                command_timeout=8,                     # 命令超时8秒
                max_queries=500,                       # 每个连接500次查询后重建（更激进）
                max_inactive_connection_lifetime=30.0, # 非活动连接30秒后关闭（更激进）
                max_cached_statement_lifetime=0,       # 不缓存prepared statements
                timeout=5.0,                           # 获取连接超时5秒
            )
            logger.debug("✅ 数据库连接池已创建 (min=1, max=2, 极短生命周期)")

    async def disconnect(self):
        """断开数据库连接"""
        if self.pool and not self.pool._closed:
            await self.pool.close()
            self.pool = None
            logger.debug("✅ 数据库连接池已关闭")

    async def reset_pool(self):
        """重置连接池（清理僵尸连接）"""
        logger.info("🔄 重置数据库连接池...")
        await self.disconnect()
        await self.connect()
        logger.success("✅ 连接池已重置")

    async def save_stop(self, symbol: str, entry_price: float, stop_loss: float,
                       take_profit: float, atr: float = None, quantity: int = None,
                       strategy: str = 'advanced_technical'):
        """保存止损止盈设置"""
        await self.connect()

        try:
            async with self.pool.acquire() as conn:
                # 先将该标的之前的活跃记录设为cancelled
                await conn.execute("""
                    UPDATE position_stops
                    SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                    WHERE symbol = $1 AND status = 'active'
                """, symbol)

                # 插入新记录
                await conn.execute("""
                    INSERT INTO position_stops (
                        symbol, entry_price, stop_loss, take_profit,
                        atr, quantity, strategy, status
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'active')
                """, symbol, entry_price, stop_loss, take_profit, atr, quantity, strategy)

            logger.info(f"✅ 已保存 {symbol} 的止损止盈设置到数据库")

        except Exception as e:
            logger.error(f"保存止损止盈失败: {e}")

    async def load_active_stops(self) -> Dict[str, Dict]:
        """加载所有活跃的止损止盈设置"""
        await self.connect()

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT symbol, entry_price, stop_loss, take_profit, atr, quantity
                    FROM position_stops
                    WHERE status = 'active'
                """)

            stops = {}
            for row in rows:
                stops[row['symbol']] = {
                    'entry_price': float(row['entry_price']),
                    'stop_loss': float(row['stop_loss']),
                    'take_profit': float(row['take_profit']),
                    'atr': float(row['atr']) if row['atr'] else None,
                    'quantity': row['quantity']
                }

            logger.info(f"📂 从数据库加载了 {len(stops)} 个止损止盈设置")
            return stops

        except Exception as e:
            logger.error(f"加载止损止盈失败: {e}")
            return {}

    async def update_stop_status(self, symbol: str, status: str, exit_price: float = None, pnl: float = None):
        """更新止损止盈状态"""
        await self.connect()

        try:
            async with self.pool.acquire() as conn:
                if exit_price:
                    await conn.execute("""
                        UPDATE position_stops
                        SET status = $2, exit_price = $3, exit_time = CURRENT_TIMESTAMP,
                            pnl = $4, updated_at = CURRENT_TIMESTAMP
                        WHERE symbol = $1 AND status = 'active'
                    """, symbol, status, exit_price, pnl)
                else:
                    await conn.execute("""
                        UPDATE position_stops
                        SET status = $2, updated_at = CURRENT_TIMESTAMP
                        WHERE symbol = $1 AND status = 'active'
                    """, symbol, status)

            logger.info(f"✅ 已更新 {symbol} 的止损止盈状态为 {status}")

        except Exception as e:
            logger.error(f"更新止损止盈状态失败: {e}")

    async def remove_stop(self, symbol: str):
        """移除止损止盈设置（标记为已取消）"""
        await self.update_stop_status(symbol, 'cancelled')

    async def get_stop_for_symbol(self, symbol: str) -> Optional[Dict]:
        """获取特定标的的活跃止损止盈设置"""
        await self.connect()

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT entry_price, stop_loss, take_profit, atr, quantity
                    FROM position_stops
                    WHERE symbol = $1 AND status = 'active'
                """, symbol)

            if row:
                return {
                    'entry_price': float(row['entry_price']),
                    'stop_loss': float(row['stop_loss']),
                    'take_profit': float(row['take_profit']),
                    'atr': float(row['atr']) if row['atr'] else None,
                    'quantity': row['quantity']
                }
            return None

        except Exception as e:
            logger.error(f"获取止损止盈设置失败: {e}")
            return None

    async def cleanup_old_records(self, days: int = 30):
        """清理旧记录"""
        await self.connect()

        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute("""
                    DELETE FROM position_stops
                    WHERE status IN ('stopped_out', 'took_profit', 'cancelled')
                    AND updated_at < CURRENT_TIMESTAMP - INTERVAL '%s days'
                """ % days)

            logger.info(f"清理了 {result.split()[-1]} 条旧止损止盈记录")

        except Exception as e:
            logger.error(f"清理旧记录失败: {e}")

    # === 兼容方法（用于order_executor和signal_generator） ===

    async def set_position_stops(self, account_id: str, symbol: str,
                                 stop_loss: float, take_profit: float) -> None:
        """
        设置持仓的止损止盈（兼容方法）

        Args:
            account_id: 账户ID（当前未使用，保留以兼容）
            symbol: 标的代码
            stop_loss: 止损价
            take_profit: 止盈价
        """
        # 获取当前价作为入场价（如果可能的话从数据库获取）
        entry_price = (stop_loss + take_profit) / 2  # 简单估算

        await self.save_stop(
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit
        )

    async def get_position_stops(self, account_id: str, symbol: str) -> Optional[Dict]:
        """
        获取持仓的止损止盈（兼容方法）

        Args:
            account_id: 账户ID（当前未使用，保留以兼容）
            symbol: 标的代码

        Returns:
            包含stop_loss和take_profit的字典，如果不存在返回None
        """
        return await self.get_stop_for_symbol(symbol)