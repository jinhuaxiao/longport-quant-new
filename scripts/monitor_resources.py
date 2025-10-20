#!/usr/bin/env python3
"""监控交易脚本的资源使用"""

import os
import sys
import time
import psutil
from datetime import datetime


def get_process_by_name(name):
    """根据名称查找进程"""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = ' '.join(proc.info['cmdline'] or [])
            if name in cmdline:
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None


def monitor_process(proc):
    """监控进程资源使用"""
    try:
        # 打开的文件描述符
        open_files = len(proc.open_files())

        # 网络连接
        connections = len(proc.connections())

        # 内存使用
        mem_info = proc.memory_info()
        mem_mb = mem_info.rss / 1024 / 1024

        # CPU 使用率
        cpu_percent = proc.cpu_percent(interval=0.1)

        # 线程数
        num_threads = proc.num_threads()

        return {
            'open_files': open_files,
            'connections': connections,
            'memory_mb': mem_mb,
            'cpu_percent': cpu_percent,
            'num_threads': num_threads
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def main():
    script_name = "advanced_technical_trading.py"

    print(f"{'='*70}")
    print(f"交易脚本资源监控")
    print(f"{'='*70}")
    print()

    # 查找进程
    proc = get_process_by_name(script_name)

    if not proc:
        print(f"❌ 未找到运行中的 {script_name}")
        print(f"请先启动: python3 scripts/{script_name} --builtin")
        sys.exit(1)

    print(f"✅ 找到进程: PID={proc.pid}")
    print()

    # 系统限制
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        print(f"系统文件描述符限制: {soft} (软限制), {hard} (硬限制)")
    except:
        pass

    print()
    print("实时监控 (按 Ctrl+C 停止)...")
    print(f"{'时间':<20} {'文件描述符':<15} {'网络连接':<12} {'内存(MB)':<12} {'CPU%':<8} {'线程':<8}")
    print("-" * 70)

    max_open_files = 0
    max_connections = 0

    try:
        while True:
            stats = monitor_process(proc)

            if not stats:
                print("\n进程已退出")
                break

            # 记录峰值
            max_open_files = max(max_open_files, stats['open_files'])
            max_connections = max(max_connections, stats['connections'])

            # 显示当前状态
            timestamp = datetime.now().strftime("%H:%M:%S")

            # 警告标记
            fd_warning = "⚠️" if stats['open_files'] > 500 else ""
            conn_warning = "⚠️" if stats['connections'] > 100 else ""
            mem_warning = "⚠️" if stats['memory_mb'] > 1000 else ""

            print(f"{timestamp:<20} "
                  f"{stats['open_files']:<12}{fd_warning:<3} "
                  f"{stats['connections']:<10}{conn_warning:<2} "
                  f"{stats['memory_mb']:<10.1f}{mem_warning:<2} "
                  f"{stats['cpu_percent']:<8.1f} "
                  f"{stats['num_threads']:<8}")

            time.sleep(5)  # 每5秒更新一次

    except KeyboardInterrupt:
        print("\n")
        print("=" * 70)
        print("监控统计:")
        print(f"  文件描述符峰值: {max_open_files}")
        print(f"  网络连接峰值: {max_connections}")
        print("=" * 70)


if __name__ == "__main__":
    main()
