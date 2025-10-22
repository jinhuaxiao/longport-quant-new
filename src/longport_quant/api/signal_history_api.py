"""
信号历史API端点

提供信号历史查询、统计和分析功能
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from datetime import datetime, timedelta
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from loguru import logger

from longport_quant.config import get_settings
from longport_quant.models.signal_history import SignalHistory, SignalRecorder

router = APIRouter()

# 数据库配置
settings = get_settings()
engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine)


class SignalQueryParams(BaseModel):
    """信号查询参数"""
    limit: int = 100
    offset: int = 0
    symbol: Optional[str] = None
    action: Optional[str] = None
    min_score: Optional[float] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_executed: Optional[bool] = None


@router.get("/signals/recent")
async def get_recent_signals(
    limit: int = Query(100, ge=1, le=1000, description="返回数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    symbol: Optional[str] = Query(None, description="股票代码"),
    action: Optional[str] = Query(None, description="操作类型 BUY/SELL"),
    min_score: Optional[float] = Query(None, ge=0, le=100, description="最低评分"),
    is_executed: Optional[bool] = Query(None, description="是否已执行")
):
    """
    获取最近的信号记录

    支持多种过滤条件
    """
    try:
        db = SessionLocal()
        recorder = SignalRecorder(db)

        query = db.query(SignalHistory).order_by(SignalHistory.timestamp.desc())

        # 应用过滤条件
        if symbol:
            query = query.filter(SignalHistory.symbol == symbol)

        if action:
            query = query.filter(SignalHistory.action == action)

        if min_score is not None:
            query = query.filter(SignalHistory.signal_score >= min_score)

        if is_executed is not None:
            query = query.filter(SignalHistory.is_executed == is_executed)

        # 分页
        total = query.count()
        signals = query.offset(offset).limit(limit).all()

        db.close()

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "signals": [signal.to_dict() for signal in signals]
        }

    except Exception as e:
        logger.error(f"获取信号历史失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/signals/stats")
async def get_signal_stats(
    days: int = Query(30, ge=1, le=365, description="统计天数"),
    symbol: Optional[str] = Query(None, description="股票代码")
):
    """
    获取信号统计数据

    包括总信号数、执行率、胜率等
    """
    try:
        db = SessionLocal()
        recorder = SignalRecorder(db)

        start_date = datetime.now() - timedelta(days=days)

        # 基础统计
        stats = recorder.get_signal_stats(start_date=start_date)

        # 表现统计
        performance = recorder.get_signal_performance(symbol=symbol, days=days)

        db.close()

        return {
            "period_days": days,
            "symbol": symbol,
            "stats": stats,
            "performance": performance
        }

    except Exception as e:
        logger.error(f"获取信号统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/signals/by-symbol/{symbol}")
async def get_signals_by_symbol(
    symbol: str,
    limit: int = Query(50, ge=1, le=500),
    days: int = Query(30, ge=1, le=365)
):
    """
    获取指定股票的信号历史

    用于分析特定股票的信号表现
    """
    try:
        db = SessionLocal()

        start_date = datetime.now() - timedelta(days=days)

        signals = db.query(SignalHistory).filter(
            SignalHistory.symbol == symbol,
            SignalHistory.timestamp >= start_date
        ).order_by(SignalHistory.timestamp.desc()).limit(limit).all()

        # 统计
        total_signals = len(signals)
        buy_signals = len([s for s in signals if s.action == 'BUY'])
        sell_signals = len([s for s in signals if s.action == 'SELL'])
        executed = len([s for s in signals if s.is_executed])

        # 平均评分
        avg_score = sum(s.signal_score for s in signals) / total_signals if total_signals > 0 else 0

        db.close()

        return {
            "symbol": symbol,
            "period_days": days,
            "total_signals": total_signals,
            "buy_signals": buy_signals,
            "sell_signals": sell_signals,
            "executed_signals": executed,
            "execution_rate": (executed / total_signals * 100) if total_signals > 0 else 0,
            "average_score": avg_score,
            "signals": [signal.to_dict() for signal in signals]
        }

    except Exception as e:
        logger.error(f"获取股票信号历史失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/signals/timeline")
async def get_signal_timeline(
    days: int = Query(7, ge=1, le=90, description="时间范围（天）"),
    symbol: Optional[str] = Query(None, description="股票代码"),
    granularity: str = Query("hour", description="粒度 hour/day")
):
    """
    获取信号时间线数据

    用于可视化信号生成趋势
    """
    try:
        from sqlalchemy import func
        from collections import defaultdict

        db = SessionLocal()

        start_date = datetime.now() - timedelta(days=days)

        query = db.query(SignalHistory).filter(SignalHistory.timestamp >= start_date)

        if symbol:
            query = query.filter(SignalHistory.symbol == symbol)

        signals = query.all()

        # 按时间分组
        timeline = defaultdict(lambda: {"buy": 0, "sell": 0, "total": 0})

        for signal in signals:
            if granularity == "hour":
                key = signal.timestamp.strftime("%Y-%m-%d %H:00")
            else:  # day
                key = signal.timestamp.strftime("%Y-%m-%d")

            timeline[key]["total"] += 1
            if signal.action == "BUY":
                timeline[key]["buy"] += 1
            elif signal.action == "SELL":
                timeline[key]["sell"] += 1

        # 转换为列表
        timeline_list = [
            {"timestamp": key, **value}
            for key, value in sorted(timeline.items())
        ]

        db.close()

        return {
            "granularity": granularity,
            "period_days": days,
            "symbol": symbol,
            "timeline": timeline_list
        }

    except Exception as e:
        logger.error(f"获取信号时间线失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/signals/top-performers")
async def get_top_performers(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(10, ge=1, le=50),
    sort_by: str = Query("win_rate", description="排序字段 win_rate/total_pnl/signal_count")
):
    """
    获取表现最好的股票

    按胜率、总盈亏或信号数量排序
    """
    try:
        from sqlalchemy import func
        from collections import defaultdict

        db = SessionLocal()

        start_date = datetime.now() - timedelta(days=days)

        # 查询已执行且有盈亏数据的信号
        signals = db.query(SignalHistory).filter(
            SignalHistory.timestamp >= start_date,
            SignalHistory.is_executed == True,
            SignalHistory.pnl.isnot(None)
        ).all()

        # 按股票分组统计
        symbol_stats = defaultdict(lambda: {
            "symbol": "",
            "signal_count": 0,
            "executed_count": 0,
            "profitable_count": 0,
            "loss_count": 0,
            "total_pnl": 0.0,
            "win_rate": 0.0,
            "avg_score": 0.0,
            "scores": []
        })

        for signal in signals:
            stats = symbol_stats[signal.symbol]
            stats["symbol"] = signal.symbol
            stats["signal_count"] += 1
            stats["executed_count"] += 1
            stats["scores"].append(signal.signal_score)

            if signal.pnl and signal.pnl > 0:
                stats["profitable_count"] += 1
            elif signal.pnl and signal.pnl < 0:
                stats["loss_count"] += 1

            if signal.pnl:
                stats["total_pnl"] += signal.pnl

        # 计算衍生指标
        for symbol, stats in symbol_stats.items():
            if stats["executed_count"] > 0:
                stats["win_rate"] = stats["profitable_count"] / stats["executed_count"] * 100
            if stats["scores"]:
                stats["avg_score"] = sum(stats["scores"]) / len(stats["scores"])
            del stats["scores"]  # 删除临时数据

        # 排序
        sorted_symbols = sorted(
            symbol_stats.values(),
            key=lambda x: x.get(sort_by, 0),
            reverse=True
        )

        db.close()

        return {
            "period_days": days,
            "sort_by": sort_by,
            "top_performers": sorted_symbols[:limit]
        }

    except Exception as e:
        logger.error(f"获取最佳表现股票失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/signals/{signal_id}")
async def get_signal_detail(signal_id: int):
    """
    获取单个信号的详细信息

    包括所有技术指标和执行详情
    """
    try:
        db = SessionLocal()

        signal = db.query(SignalHistory).filter_by(id=signal_id).first()

        if not signal:
            raise HTTPException(status_code=404, detail="Signal not found")

        db.close()

        return signal.to_dict()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取信号详情失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/signals/cleanup")
async def cleanup_old_signals(
    days: int = Query(90, ge=30, le=365, description="保留天数"),
    dry_run: bool = Query(True, description="是否为演练模式")
):
    """
    清理旧的信号记录

    删除指定天数之前的信号（默认90天）
    """
    try:
        db = SessionLocal()

        cutoff_date = datetime.now() - timedelta(days=days)

        query = db.query(SignalHistory).filter(SignalHistory.timestamp < cutoff_date)

        count = query.count()

        if not dry_run:
            query.delete()
            db.commit()
            message = f"已删除 {count} 条信号记录"
        else:
            message = f"演练模式：将删除 {count} 条信号记录"

        db.close()

        return {
            "dry_run": dry_run,
            "cutoff_date": cutoff_date.isoformat(),
            "count": count,
            "message": message
        }

    except Exception as e:
        logger.error(f"清理信号记录失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
