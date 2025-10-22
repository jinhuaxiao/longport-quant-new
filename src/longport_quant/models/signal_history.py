"""
信号历史记录模型

用于存储和查询所有生成的交易信号，支持回溯分析
"""

from datetime import datetime
from typing import Dict, List, Optional
from decimal import Decimal
from sqlalchemy import Column, String, Float, Integer, DateTime, JSON, Boolean, Text, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session
from loguru import logger

Base = declarative_base()


class SignalHistory(Base):
    """交易信号历史记录表"""

    __tablename__ = "signal_history"

    # 主键
    id = Column(Integer, primary_key=True, autoincrement=True)

    # 基本信息
    timestamp = Column(DateTime, nullable=False, index=True, comment="信号生成时间")
    symbol = Column(String(20), nullable=False, index=True, comment="股票代码")
    action = Column(String(10), nullable=False, index=True, comment="操作类型 BUY/SELL")

    # 价格信息
    price = Column(Float, nullable=False, comment="当前价格")
    target_price = Column(Float, nullable=True, comment="目标价格")

    # 信号评分
    signal_score = Column(Float, nullable=False, index=True, comment="信号强度评分 0-100")
    confidence = Column(Float, nullable=True, comment="置信度 0-1")

    # 技术指标（JSON存储）
    indicators = Column(JSON, nullable=True, comment="技术指标数据")
    """
    示例：
    {
        "rsi": 65.5,
        "macd": 0.25,
        "bollinger_position": 0.8,
        "volume_ratio": 1.5,
        "ma_fast": 182.5,
        "ma_slow": 180.3
    }
    """

    # 策略信息
    strategy_name = Column(String(50), nullable=True, comment="策略名称")
    strategy_params = Column(JSON, nullable=True, comment="策略参数")

    # 执行状态
    is_executed = Column(Boolean, default=False, index=True, comment="是否已执行")
    executed_at = Column(DateTime, nullable=True, comment="执行时间")
    execution_price = Column(Float, nullable=True, comment="实际成交价格")
    execution_quantity = Column(Integer, nullable=True, comment="实际成交数量")

    # 执行结果
    order_id = Column(String(50), nullable=True, comment="订单ID")
    execution_status = Column(String(20), nullable=True, comment="执行状态 success/failed/pending")
    execution_error = Column(Text, nullable=True, comment="执行错误信息")

    # 收益追踪（用于回测）
    entry_price = Column(Float, nullable=True, comment="入场价格")
    exit_price = Column(Float, nullable=True, comment="出场价格")
    pnl = Column(Float, nullable=True, comment="盈亏")
    pnl_percent = Column(Float, nullable=True, comment="盈亏百分比")

    # 市场环境
    market_trend = Column(String(20), nullable=True, comment="市场趋势 bullish/bearish/neutral")
    volatility = Column(Float, nullable=True, comment="波动率")

    # 备注
    notes = Column(Text, nullable=True, comment="备注信息")

    # 元数据
    created_at = Column(DateTime, default=datetime.now, comment="记录创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="记录更新时间")

    # 索引
    __table_args__ = (
        Index('idx_symbol_timestamp', 'symbol', 'timestamp'),
        Index('idx_action_executed', 'action', 'is_executed'),
        Index('idx_score_timestamp', 'signal_score', 'timestamp'),
    )

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'symbol': self.symbol,
            'action': self.action,
            'price': self.price,
            'target_price': self.target_price,
            'signal_score': self.signal_score,
            'confidence': self.confidence,
            'indicators': self.indicators,
            'strategy_name': self.strategy_name,
            'strategy_params': self.strategy_params,
            'is_executed': self.is_executed,
            'executed_at': self.executed_at.isoformat() if self.executed_at else None,
            'execution_price': self.execution_price,
            'execution_quantity': self.execution_quantity,
            'order_id': self.order_id,
            'execution_status': self.execution_status,
            'execution_error': self.execution_error,
            'entry_price': self.entry_price,
            'exit_price': self.exit_price,
            'pnl': self.pnl,
            'pnl_percent': self.pnl_percent,
            'market_trend': self.market_trend,
            'volatility': self.volatility,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class SignalRecorder:
    """信号记录器 - 负责保存和查询信号历史"""

    def __init__(self, db_session: Session):
        """
        初始化信号记录器

        Args:
            db_session: SQLAlchemy数据库会话
        """
        self.db = db_session

    def record_signal(
        self,
        symbol: str,
        action: str,
        price: float,
        signal_score: float,
        indicators: Optional[Dict] = None,
        strategy_name: Optional[str] = None,
        **kwargs
    ) -> SignalHistory:
        """
        记录一个交易信号

        Args:
            symbol: 股票代码
            action: 操作类型 (BUY/SELL)
            price: 当前价格
            signal_score: 信号评分
            indicators: 技术指标
            strategy_name: 策略名称
            **kwargs: 其他可选参数

        Returns:
            SignalHistory对象
        """
        try:
            signal = SignalHistory(
                timestamp=datetime.now(),
                symbol=symbol,
                action=action,
                price=price,
                signal_score=signal_score,
                indicators=indicators,
                strategy_name=strategy_name,
                **kwargs
            )

            self.db.add(signal)
            self.db.commit()
            self.db.refresh(signal)

            logger.debug(f"信号已记录: {symbol} {action} @{price} score={signal_score}")

            return signal

        except Exception as e:
            logger.error(f"记录信号失败: {e}")
            self.db.rollback()
            raise

    def update_execution(
        self,
        signal_id: int,
        executed_at: datetime,
        execution_price: float,
        execution_quantity: int,
        order_id: str,
        execution_status: str = "success",
        execution_error: Optional[str] = None
    ):
        """
        更新信号的执行状态

        Args:
            signal_id: 信号ID
            executed_at: 执行时间
            execution_price: 成交价格
            execution_quantity: 成交数量
            order_id: 订单ID
            execution_status: 执行状态
            execution_error: 错误信息
        """
        try:
            signal = self.db.query(SignalHistory).filter_by(id=signal_id).first()
            if signal:
                signal.is_executed = True
                signal.executed_at = executed_at
                signal.execution_price = execution_price
                signal.execution_quantity = execution_quantity
                signal.order_id = order_id
                signal.execution_status = execution_status
                signal.execution_error = execution_error

                self.db.commit()

                logger.debug(f"信号执行状态已更新: ID={signal_id} status={execution_status}")

        except Exception as e:
            logger.error(f"更新执行状态失败: {e}")
            self.db.rollback()
            raise

    def get_recent_signals(
        self,
        limit: int = 100,
        symbol: Optional[str] = None,
        action: Optional[str] = None,
        min_score: Optional[float] = None
    ) -> List[SignalHistory]:
        """
        获取最近的信号

        Args:
            limit: 返回数量限制
            symbol: 过滤股票代码
            action: 过滤操作类型
            min_score: 最低评分

        Returns:
            信号列表
        """
        query = self.db.query(SignalHistory).order_by(SignalHistory.timestamp.desc())

        if symbol:
            query = query.filter(SignalHistory.symbol == symbol)

        if action:
            query = query.filter(SignalHistory.action == action)

        if min_score is not None:
            query = query.filter(SignalHistory.signal_score >= min_score)

        return query.limit(limit).all()

    def get_signal_stats(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict:
        """
        获取信号统计数据

        Args:
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            统计数据字典
        """
        query = self.db.query(SignalHistory)

        if start_date:
            query = query.filter(SignalHistory.timestamp >= start_date)

        if end_date:
            query = query.filter(SignalHistory.timestamp <= end_date)

        total_signals = query.count()
        buy_signals = query.filter(SignalHistory.action == 'BUY').count()
        sell_signals = query.filter(SignalHistory.action == 'SELL').count()
        executed_signals = query.filter(SignalHistory.is_executed == True).count()

        # 计算平均评分
        avg_score = self.db.query(
            func.avg(SignalHistory.signal_score)
        ).filter(*query.whereclause.clauses if hasattr(query.whereclause, 'clauses') else []).scalar() or 0

        # 执行率
        execution_rate = (executed_signals / total_signals * 100) if total_signals > 0 else 0

        return {
            'total_signals': total_signals,
            'buy_signals': buy_signals,
            'sell_signals': sell_signals,
            'executed_signals': executed_signals,
            'execution_rate': execution_rate,
            'average_score': float(avg_score),
        }

    def get_signal_performance(
        self,
        symbol: Optional[str] = None,
        days: int = 30
    ) -> Dict:
        """
        获取信号表现统计

        Args:
            symbol: 股票代码（可选）
            days: 统计天数

        Returns:
            表现统计数据
        """
        from datetime import timedelta
        from sqlalchemy import func

        start_date = datetime.now() - timedelta(days=days)

        query = self.db.query(SignalHistory).filter(
            SignalHistory.timestamp >= start_date,
            SignalHistory.is_executed == True,
            SignalHistory.pnl.isnot(None)
        )

        if symbol:
            query = query.filter(SignalHistory.symbol == symbol)

        # 盈利信号数
        profitable_count = query.filter(SignalHistory.pnl > 0).count()
        loss_count = query.filter(SignalHistory.pnl < 0).count()
        total_executed = query.count()

        # 总盈亏
        total_pnl = self.db.query(func.sum(SignalHistory.pnl)).filter(
            *query.whereclause.clauses if hasattr(query.whereclause, 'clauses') else []
        ).scalar() or 0

        # 平均盈亏
        avg_pnl = self.db.query(func.avg(SignalHistory.pnl)).filter(
            *query.whereclause.clauses if hasattr(query.whereclause, 'clauses') else []
        ).scalar() or 0

        # 胜率
        win_rate = (profitable_count / total_executed * 100) if total_executed > 0 else 0

        return {
            'total_executed': total_executed,
            'profitable_count': profitable_count,
            'loss_count': loss_count,
            'win_rate': win_rate,
            'total_pnl': float(total_pnl),
            'average_pnl': float(avg_pnl),
        }
