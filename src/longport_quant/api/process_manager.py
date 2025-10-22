"""
进程管理API - 用于启动、停止和监控交易系统进程

功能：
1. 启动/停止 signal_generator.py
2. 启动/停止 order_executor.py
3. 查询进程状态
4. 管理Redis队列
"""

import os
import signal
import subprocess
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
from loguru import logger


class ProcessManager:
    """交易系统进程管理器"""

    def __init__(self, project_root: Optional[Path] = None):
        """
        初始化进程管理器

        Args:
            project_root: 项目根目录，默认自动检测
        """
        if project_root is None:
            # 从当前文件位置推导项目根目录
            self.project_root = Path(__file__).parent.parent.parent.parent
        else:
            self.project_root = project_root

        self.scripts_dir = self.project_root / "scripts"
        self.logs_dir = self.project_root / "logs"

        # 确保日志目录存在
        self.logs_dir.mkdir(exist_ok=True)

        # PID文件路径
        self.signal_generator_pid_file = self.logs_dir / "signal_generator.pid"
        self.order_executor_pid_file = self.logs_dir / "order_executor.pid"

    def start_signal_generator(self) -> Dict[str, any]:
        """
        启动信号生成器进程

        Returns:
            包含进程信息的字典
        """
        if self.is_signal_generator_running():
            return {
                "success": False,
                "message": "Signal generator is already running",
                "pid": self.get_signal_generator_pid()
            }

        try:
            script_path = self.scripts_dir / "signal_generator.py"
            log_file = self.logs_dir / f"signal_generator_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

            # 启动进程（后台运行）
            process = subprocess.Popen(
                ["python3", str(script_path)],
                stdout=open(log_file, 'w'),
                stderr=subprocess.STDOUT,
                cwd=str(self.project_root),
                start_new_session=True  # 创建新的进程组
            )

            # 保存PID
            self.signal_generator_pid_file.write_text(str(process.pid))

            logger.info(f"Signal generator started with PID {process.pid}")

            return {
                "success": True,
                "message": "Signal generator started",
                "pid": process.pid,
                "log_file": str(log_file)
            }

        except Exception as e:
            logger.error(f"Failed to start signal generator: {e}")
            return {
                "success": False,
                "message": f"Failed to start: {str(e)}"
            }

    def start_order_executor(self) -> Dict[str, any]:
        """
        启动订单执行器进程

        Returns:
            包含进程信息的字典
        """
        if self.is_order_executor_running():
            return {
                "success": False,
                "message": "Order executor is already running",
                "pid": self.get_order_executor_pid()
            }

        try:
            script_path = self.scripts_dir / "order_executor.py"
            log_file = self.logs_dir / f"order_executor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

            # 启动进程（后台运行）
            process = subprocess.Popen(
                ["python3", str(script_path)],
                stdout=open(log_file, 'w'),
                stderr=subprocess.STDOUT,
                cwd=str(self.project_root),
                start_new_session=True
            )

            # 保存PID
            self.order_executor_pid_file.write_text(str(process.pid))

            logger.info(f"Order executor started with PID {process.pid}")

            return {
                "success": True,
                "message": "Order executor started",
                "pid": process.pid,
                "log_file": str(log_file)
            }

        except Exception as e:
            logger.error(f"Failed to start order executor: {e}")
            return {
                "success": False,
                "message": f"Failed to start: {str(e)}"
            }

    def stop_signal_generator(self) -> Dict[str, any]:
        """
        停止信号生成器进程

        Returns:
            操作结果
        """
        pid = self.get_signal_generator_pid()
        if pid is None:
            return {
                "success": False,
                "message": "Signal generator is not running"
            }

        try:
            # 尝试优雅终止
            os.kill(pid, signal.SIGTERM)

            # 清理PID文件
            if self.signal_generator_pid_file.exists():
                self.signal_generator_pid_file.unlink()

            logger.info(f"Signal generator stopped (PID {pid})")

            return {
                "success": True,
                "message": "Signal generator stopped",
                "pid": pid
            }

        except ProcessLookupError:
            # 进程已经不存在
            if self.signal_generator_pid_file.exists():
                self.signal_generator_pid_file.unlink()
            return {
                "success": True,
                "message": "Signal generator was not running"
            }
        except Exception as e:
            logger.error(f"Failed to stop signal generator: {e}")
            return {
                "success": False,
                "message": f"Failed to stop: {str(e)}"
            }

    def stop_order_executor(self) -> Dict[str, any]:
        """
        停止订单执行器进程

        Returns:
            操作结果
        """
        pid = self.get_order_executor_pid()
        if pid is None:
            return {
                "success": False,
                "message": "Order executor is not running"
            }

        try:
            # 尝试优雅终止
            os.kill(pid, signal.SIGTERM)

            # 清理PID文件
            if self.order_executor_pid_file.exists():
                self.order_executor_pid_file.unlink()

            logger.info(f"Order executor stopped (PID {pid})")

            return {
                "success": True,
                "message": "Order executor stopped",
                "pid": pid
            }

        except ProcessLookupError:
            if self.order_executor_pid_file.exists():
                self.order_executor_pid_file.unlink()
            return {
                "success": True,
                "message": "Order executor was not running"
            }
        except Exception as e:
            logger.error(f"Failed to stop order executor: {e}")
            return {
                "success": False,
                "message": f"Failed to stop: {str(e)}"
            }

    def get_signal_generator_pid(self) -> Optional[int]:
        """获取信号生成器的PID"""
        if not self.signal_generator_pid_file.exists():
            return None

        try:
            pid = int(self.signal_generator_pid_file.read_text().strip())
            # 检查进程是否还在运行
            os.kill(pid, 0)  # 发送信号0只检查进程是否存在
            return pid
        except (ValueError, ProcessLookupError):
            # PID文件损坏或进程已不存在
            if self.signal_generator_pid_file.exists():
                self.signal_generator_pid_file.unlink()
            return None

    def get_order_executor_pid(self) -> Optional[int]:
        """获取订单执行器的PID"""
        if not self.order_executor_pid_file.exists():
            return None

        try:
            pid = int(self.order_executor_pid_file.read_text().strip())
            os.kill(pid, 0)
            return pid
        except (ValueError, ProcessLookupError):
            if self.order_executor_pid_file.exists():
                self.order_executor_pid_file.unlink()
            return None

    def is_signal_generator_running(self) -> bool:
        """检查信号生成器是否运行中"""
        return self.get_signal_generator_pid() is not None

    def is_order_executor_running(self) -> bool:
        """检查订单执行器是否运行中"""
        return self.get_order_executor_pid() is not None

    def get_process_uptime(self, pid: int) -> Optional[str]:
        """
        获取进程运行时长

        Args:
            pid: 进程ID

        Returns:
            运行时长字符串，如 "2h 15m 30s"
        """
        try:
            # 使用ps命令获取进程启动时间
            result = subprocess.run(
                ["ps", "-o", "etime=", "-p", str(pid)],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def get_system_status(self) -> Dict[str, any]:
        """
        获取完整的系统状态

        Returns:
            系统状态字典
        """
        sg_pid = self.get_signal_generator_pid()
        oe_pid = self.get_order_executor_pid()

        return {
            "status": "running" if (sg_pid and oe_pid) else "stopped",
            "signal_generator": {
                "running": sg_pid is not None,
                "pid": sg_pid,
                "uptime": self.get_process_uptime(sg_pid) if sg_pid else None
            },
            "order_executor": {
                "running": oe_pid is not None,
                "pid": oe_pid,
                "uptime": self.get_process_uptime(oe_pid) if oe_pid else None
            }
        }

    def start_system(self) -> Dict[str, any]:
        """
        启动整个交易系统（信号生成器 + 订单执行器）

        Returns:
            启动结果
        """
        results = {
            "signal_generator": self.start_signal_generator(),
            "order_executor": self.start_order_executor()
        }

        success = results["signal_generator"]["success"] and results["order_executor"]["success"]

        return {
            "success": success,
            "message": "System started" if success else "System start failed",
            "details": results
        }

    def stop_system(self) -> Dict[str, any]:
        """
        停止整个交易系统

        Returns:
            停止结果
        """
        results = {
            "signal_generator": self.stop_signal_generator(),
            "order_executor": self.stop_order_executor()
        }

        return {
            "success": True,
            "message": "System stopped",
            "details": results
        }
