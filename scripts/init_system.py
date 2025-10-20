#!/usr/bin/env python3
"""系统初始化脚本 - 完成所有基础数据准备"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger
import subprocess


class SystemInitializer:
    """系统初始化器"""

    def __init__(self):
        self.results = {}
        self.start_time = datetime.now()

    def log_step(self, step_name: str, status: str = "开始"):
        """记录步骤"""
        logger.info(f"[{step_name}] {status}")
        if status == "完成":
            self.results[step_name] = "✓"
        elif status.startswith("失败"):
            self.results[step_name] = f"✗ {status}"

    async def check_environment(self):
        """检查环境配置"""
        self.log_step("环境检查", "开始")

        issues = []

        # 检查 .env 文件
        env_file = Path(".env")
        if not env_file.exists():
            issues.append(".env 文件不存在")
        else:
            with open(env_file) as f:
                content = f.read()
                if "LONGPORT_APP_KEY" not in content:
                    issues.append("缺少 LONGPORT_APP_KEY")
                if "DATABASE_DSN" not in content:
                    issues.append("缺少 DATABASE_DSN")

        # 检查配置文件
        watchlist = Path("configs/watchlist.yml")
        if not watchlist.exists():
            issues.append("configs/watchlist.yml 不存在")

        if issues:
            self.log_step("环境检查", f"失败: {', '.join(issues)}")
            return False

        self.log_step("环境检查", "完成")
        return True

    async def init_database(self):
        """初始化数据库"""
        self.log_step("数据库初始化", "开始")

        try:
            # 运行数据库迁移
            result = subprocess.run(
                ["python", "-m", "alembic", "upgrade", "head"],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                self.log_step("数据库初始化", f"失败: {result.stderr}")
                return False

            # 创建分区表
            logger.info("创建分区表...")
            subprocess.run(["python", "scripts/create_missing_partitions.py"])
            subprocess.run(["python", "scripts/create_daily_partitions.py"])

            self.log_step("数据库初始化", "完成")
            return True

        except Exception as e:
            self.log_step("数据库初始化", f"失败: {e}")
            return False

    async def sync_security_universe(self):
        """同步证券列表"""
        self.log_step("证券列表同步", "开始")

        try:
            # 同步港股
            logger.info("同步港股证券列表...")
            result = subprocess.run(
                ["python", "scripts/sync_security_data.py", "hk", "--limit", "50"],
                capture_output=True,
                text=True,
                timeout=60
            )

            if "同步任务完成" not in result.stdout:
                logger.warning("港股同步可能未完成")

            # 同步美股
            logger.info("同步美股证券列表...")
            result = subprocess.run(
                ["python", "scripts/sync_security_data.py", "us", "--limit", "30"],
                capture_output=True,
                text=True,
                timeout=60
            )

            self.log_step("证券列表同步", "完成")
            return True

        except Exception as e:
            self.log_step("证券列表同步", f"失败: {e}")
            return False

    async def sync_historical_data(self):
        """同步历史K线数据"""
        self.log_step("历史数据同步", "开始")

        try:
            # 同步最近1年的日线数据
            logger.info("同步历史日线数据（1年）...")
            result = subprocess.run(
                ["python", "scripts/sync_historical_klines.py",
                 "--limit", "10",  # 限制10个股票
                 "--years", "1",
                 "--batch-size", "5"],
                capture_output=True,
                text=True,
                timeout=120
            )

            if "同步完成" in result.stdout:
                self.log_step("历史数据同步", "完成")
                return True
            else:
                self.log_step("历史数据同步", "部分完成")
                return True

        except Exception as e:
            self.log_step("历史数据同步", f"失败: {e}")
            return False

    async def init_watchlist(self):
        """初始化监控列表"""
        self.log_step("监控列表初始化", "开始")

        try:
            # 运行初始化脚本
            result = subprocess.run(
                ["python", "scripts/init_watchlist.py"],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                self.log_step("监控列表初始化", "完成")
                return True
            else:
                self.log_step("监控列表初始化", f"失败: {result.stderr}")
                return False

        except Exception as e:
            self.log_step("监控列表初始化", f"失败: {e}")
            return False

    async def verify_data(self):
        """验证数据完整性"""
        self.log_step("数据验证", "开始")

        try:
            # 运行数据检查脚本
            result = subprocess.run(
                ["python", "scripts/check_data.py"],
                capture_output=True,
                text=True
            )

            logger.info(result.stdout)

            # 检查关键表是否有数据
            if "表中有 0 条记录" in result.stdout:
                self.log_step("数据验证", "警告: 部分表为空")
                return False

            self.log_step("数据验证", "完成")
            return True

        except Exception as e:
            self.log_step("数据验证", f"失败: {e}")
            return False

    async def run(self):
        """运行初始化流程"""
        logger.info("=" * 60)
        logger.info("开始系统初始化")
        logger.info("=" * 60)

        steps = [
            ("环境检查", self.check_environment),
            ("数据库初始化", self.init_database),
            ("证券列表同步", self.sync_security_universe),
            ("历史数据同步", self.sync_historical_data),
            ("监控列表初始化", self.init_watchlist),
            ("数据验证", self.verify_data),
        ]

        success_count = 0
        for step_name, step_func in steps:
            try:
                result = await step_func()
                if result:
                    success_count += 1
                else:
                    logger.warning(f"{step_name} 未完全成功")
            except Exception as e:
                logger.error(f"{step_name} 执行失败: {e}")

        # 输出总结
        duration = datetime.now() - self.start_time
        logger.info("=" * 60)
        logger.info("初始化完成总结")
        logger.info("=" * 60)
        logger.info(f"总耗时: {duration}")
        logger.info(f"成功步骤: {success_count}/{len(steps)}")

        for step, status in self.results.items():
            logger.info(f"  {step}: {status}")

        if success_count == len(steps):
            logger.info("✅ 系统初始化成功！")
            return 0
        else:
            logger.warning("⚠️  系统初始化部分完成，请检查失败项")
            return 1


async def main():
    initializer = SystemInitializer()
    return await initializer.run()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)