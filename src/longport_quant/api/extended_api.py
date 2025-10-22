"""
扩展API端点 - 整合进程管理和队列管理

新增端点：
- POST /api/system/control/start - 启动交易系统
- POST /api/system/control/stop - 停止交易系统
- GET  /api/queue/stats - 获取队列统计
- POST /api/queue/clear/{queue_type} - 清空队列
- POST /api/queue/retry-failed - 重试失败的信号
- GET  /api/queue/recent - 获取最近的信号
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from loguru import logger

from longport_quant.api.process_manager import ProcessManager
from longport_quant.api.queue_manager import QueueManager
from longport_quant.config import get_settings

# 创建路由器
router = APIRouter()

# 初始化管理器
settings = get_settings()
process_manager = ProcessManager()
queue_manager = QueueManager(redis_url=settings.redis_url)


# ==================== 系统控制端点 ====================

@router.post("/system/control/start")
async def start_system():
    """启动整个交易系统（signal_generator + order_executor）"""
    try:
        result = process_manager.start_system()
        return result
    except Exception as e:
        logger.error(f"Failed to start system: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/system/control/stop")
async def stop_system():
    """停止整个交易系统"""
    try:
        result = process_manager.stop_system()
        return result
    except Exception as e:
        logger.error(f"Failed to stop system: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/system/control/pause")
async def pause_system():
    """暂停交易（仅停止订单执行器，保持信号生成）"""
    try:
        result = process_manager.stop_order_executor()
        return {
            "success": result["success"],
            "message": "Trading paused (signal generation continues)",
            "details": result
        }
    except Exception as e:
        logger.error(f"Failed to pause system: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/system/control/resume")
async def resume_system():
    """恢复交易（重启订单执行器）"""
    try:
        result = process_manager.start_order_executor()
        return {
            "success": result["success"],
            "message": "Trading resumed",
            "details": result
        }
    except Exception as e:
        logger.error(f"Failed to resume system: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/system/process-status")
async def get_process_status():
    """获取进程详细状态"""
    try:
        status = process_manager.get_system_status()
        return status
    except Exception as e:
        logger.error(f"Failed to get process status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 队列管理端点 ====================

@router.get("/queue/stats")
async def get_queue_stats():
    """获取Redis队列统计信息"""
    try:
        stats = queue_manager.get_queue_stats()
        return stats
    except Exception as e:
        logger.error(f"Failed to get queue stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/queue/clear/{queue_type}")
async def clear_queue(queue_type: str):
    """
    清空指定队列

    Args:
        queue_type: 'pending', 'processing', or 'failed'
    """
    try:
        result = queue_manager.clear_queue(queue_type)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to clear queue: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/queue/retry-failed")
async def retry_failed_signals():
    """将失败的信号重新放入待处理队列"""
    try:
        result = queue_manager.retry_failed()
        return result
    except Exception as e:
        logger.error(f"Failed to retry failed signals: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/queue/recent")
async def get_recent_signals(limit: int = 10):
    """获取最近的信号"""
    try:
        signals = queue_manager.get_recent_signals(limit=limit)
        return {"signals": signals}
    except Exception as e:
        logger.error(f"Failed to get recent signals: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/queue/clear-all")
async def clear_all_queues():
    """
    清空所有队列（危险操作）

    需要确认参数
    """
    try:
        result = queue_manager.clear_all_queues()
        return result
    except Exception as e:
        logger.error(f"Failed to clear all queues: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 进程管理端点 ====================

@router.post("/process/signal-generator/start")
async def start_signal_generator():
    """单独启动信号生成器"""
    try:
        result = process_manager.start_signal_generator()
        return result
    except Exception as e:
        logger.error(f"Failed to start signal generator: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/process/signal-generator/stop")
async def stop_signal_generator():
    """单独停止信号生成器"""
    try:
        result = process_manager.stop_signal_generator()
        return result
    except Exception as e:
        logger.error(f"Failed to stop signal generator: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/process/order-executor/start")
async def start_order_executor():
    """单独启动订单执行器"""
    try:
        result = process_manager.start_order_executor()
        return result
    except Exception as e:
        logger.error(f"Failed to start order executor: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/process/order-executor/stop")
async def stop_order_executor():
    """单独停止订单执行器"""
    try:
        result = process_manager.stop_order_executor()
        return result
    except Exception as e:
        logger.error(f"Failed to stop order executor: {e}")
        raise HTTPException(status_code=500, detail=str(e))
