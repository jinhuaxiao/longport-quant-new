"""Signal management system for trading strategies."""

from __future__ import annotations

from typing import Dict, List, Optional, Any, Set
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
from enum import Enum
import asyncio
from collections import defaultdict
import json

from loguru import logger
from longport_quant.common.types import Signal
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import TradingSignal, Position
from sqlalchemy import select, and_, or_, delete, update
from sqlalchemy.dialects.postgresql import insert


class SignalPriority(Enum):
    """Signal priority levels."""
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4


class SignalStatus(Enum):
    """Signal status."""
    PENDING = "pending"
    VALIDATED = "validated"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass
class SignalFilter:
    """Filter criteria for signal queries."""
    symbols: Optional[List[str]] = None
    strategies: Optional[List[str]] = None
    signal_types: Optional[List[str]] = None
    min_strength: Optional[float] = None
    max_strength: Optional[float] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    status: Optional[SignalStatus] = None
    executed: Optional[bool] = None


@dataclass
class SignalConflict:
    """Represents a conflict between signals."""
    signal1: Signal
    signal2: Signal
    conflict_type: str
    resolution: Optional[str] = None
    resolved_signal: Optional[Signal] = None


class SignalManager:
    """Manages trading signals across strategies."""

    def __init__(self, db: DatabaseSessionManager):
        """
        Initialize signal manager.

        Args:
            db: Database session manager
        """
        self.db = db
        self._active_signals: Dict[str, List[Signal]] = defaultdict(list)
        self._signal_history: List[Signal] = []
        self._conflict_rules: List[ConflictRule] = self._init_conflict_rules()
        self._priority_rules: List[PriorityRule] = self._init_priority_rules()

    async def add_signal(self, signal: Signal) -> bool:
        """
        Add a new signal to the system.

        Args:
            signal: Signal to add

        Returns:
            True if signal was added successfully
        """
        try:
            # Validate signal
            if not await self._validate_signal(signal):
                logger.warning(f"Signal validation failed: {signal}")
                return False

            # Check for conflicts
            conflicts = await self._check_conflicts(signal)
            if conflicts:
                resolved_signal = await self._resolve_conflicts(signal, conflicts)
                if resolved_signal is None:
                    logger.info(f"Signal rejected due to conflicts: {signal}")
                    return False
                signal = resolved_signal

            # Calculate priority
            priority = self._calculate_priority(signal)

            # Store signal
            await self._persist_signal(signal, priority)

            # Add to active signals
            self._active_signals[signal.symbol].append(signal)

            # Trigger signal processing
            await self._process_signal(signal)

            logger.info(f"Signal added: {signal.symbol} - {signal.signal_type} "
                       f"(strength: {signal.strength}, priority: {priority})")

            return True

        except Exception as e:
            logger.error(f"Error adding signal: {e}")
            return False

    async def get_active_signals(
        self,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None
    ) -> List[Signal]:
        """
        Get active signals.

        Args:
            symbol: Filter by symbol
            strategy: Filter by strategy

        Returns:
            List of active signals
        """
        signals = []

        if symbol:
            signals.extend(self._active_signals.get(symbol, []))
        else:
            for symbol_signals in self._active_signals.values():
                signals.extend(symbol_signals)

        if strategy:
            signals = [s for s in signals if s.strategy == strategy]

        return signals

    async def query_signals(
        self,
        filter_criteria: SignalFilter
    ) -> List[Dict[str, Any]]:
        """
        Query historical signals from database.

        Args:
            filter_criteria: Filter criteria

        Returns:
            List of signals matching criteria
        """
        async with self.db.session() as session:
            stmt = select(TradingSignal)

            # Apply filters
            conditions = []

            if filter_criteria.symbols:
                conditions.append(TradingSignal.symbol.in_(filter_criteria.symbols))

            if filter_criteria.strategies:
                conditions.append(TradingSignal.strategy_name.in_(filter_criteria.strategies))

            if filter_criteria.signal_types:
                conditions.append(TradingSignal.signal_type.in_(filter_criteria.signal_types))

            if filter_criteria.min_strength is not None:
                conditions.append(TradingSignal.signal_strength >= filter_criteria.min_strength)

            if filter_criteria.max_strength is not None:
                conditions.append(TradingSignal.signal_strength <= filter_criteria.max_strength)

            if filter_criteria.start_time:
                conditions.append(TradingSignal.created_at >= filter_criteria.start_time)

            if filter_criteria.end_time:
                conditions.append(TradingSignal.created_at <= filter_criteria.end_time)

            if filter_criteria.executed is not None:
                conditions.append(TradingSignal.executed == filter_criteria.executed)

            if conditions:
                stmt = stmt.where(and_(*conditions))

            stmt = stmt.order_by(TradingSignal.created_at.desc())

            result = await session.execute(stmt)
            signals = result.scalars().all()

            return [self._signal_to_dict(s) for s in signals]

    async def expire_old_signals(self, max_age_hours: int = 24) -> int:
        """
        Expire signals older than specified age.

        Args:
            max_age_hours: Maximum age in hours

        Returns:
            Number of signals expired
        """
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        expired_count = 0

        # Remove from active signals
        for symbol in list(self._active_signals.keys()):
            active = self._active_signals[symbol]
            unexpired = []

            for signal in active:
                if signal.timestamp < cutoff_time:
                    expired_count += 1
                else:
                    unexpired.append(signal)

            self._active_signals[symbol] = unexpired

        # Update database
        async with self.db.session() as session:
            stmt = update(TradingSignal).where(
                and_(
                    TradingSignal.created_at < cutoff_time,
                    TradingSignal.executed == False
                )
            ).values(executed=False)  # Mark as expired in features

            await session.execute(stmt)
            await session.commit()

        logger.info(f"Expired {expired_count} signals older than {max_age_hours} hours")
        return expired_count

    async def _validate_signal(self, signal: Signal) -> bool:
        """Validate signal before processing."""
        # Basic validation
        if not signal.symbol or not signal.signal_type:
            return False

        if signal.strength < 0 or signal.strength > 100:
            return False

        # Check stop loss and take profit logic
        if signal.stop_loss and signal.take_profit:
            if signal.signal_type in ["BUY", "STRONG_BUY"]:
                if signal.stop_loss >= signal.price_target:
                    return False
                if signal.take_profit <= signal.price_target:
                    return False
            elif signal.signal_type in ["SELL", "STRONG_SELL"]:
                if signal.stop_loss <= signal.price_target:
                    return False
                if signal.take_profit >= signal.price_target:
                    return False

        return True

    async def _check_conflicts(self, signal: Signal) -> List[SignalConflict]:
        """Check for conflicts with existing signals."""
        conflicts = []

        # Get active signals for the same symbol
        existing_signals = self._active_signals.get(signal.symbol, [])

        for existing in existing_signals:
            # Check for opposite signals
            if self._are_opposite_signals(signal, existing):
                conflict = SignalConflict(
                    signal1=signal,
                    signal2=existing,
                    conflict_type="opposite_direction"
                )
                conflicts.append(conflict)

            # Check for duplicate signals from same strategy
            if (signal.strategy == existing.strategy and
                signal.signal_type == existing.signal_type):
                conflict = SignalConflict(
                    signal1=signal,
                    signal2=existing,
                    conflict_type="duplicate"
                )
                conflicts.append(conflict)

            # Check for conflicting price targets
            if self._have_conflicting_targets(signal, existing):
                conflict = SignalConflict(
                    signal1=signal,
                    signal2=existing,
                    conflict_type="conflicting_targets"
                )
                conflicts.append(conflict)

        return conflicts

    def _are_opposite_signals(self, signal1: Signal, signal2: Signal) -> bool:
        """Check if two signals are opposite."""
        buy_signals = ["BUY", "STRONG_BUY", "ACCUMULATION", "BREAKOUT_UP", "TREND_UP"]
        sell_signals = ["SELL", "STRONG_SELL", "DISTRIBUTION", "BREAKOUT_DOWN", "TREND_DOWN"]

        signal1_is_buy = signal1.signal_type in buy_signals
        signal2_is_buy = signal2.signal_type in buy_signals

        return signal1_is_buy != signal2_is_buy

    def _have_conflicting_targets(self, signal1: Signal, signal2: Signal) -> bool:
        """Check if signals have conflicting price targets."""
        if not (signal1.price_target and signal2.price_target):
            return False

        # If both are buy signals but with very different targets
        if (signal1.signal_type in ["BUY", "STRONG_BUY"] and
            signal2.signal_type in ["BUY", "STRONG_BUY"]):
            diff_pct = abs(signal1.price_target - signal2.price_target) / signal1.price_target
            return diff_pct > 0.05  # More than 5% difference

        return False

    async def _resolve_conflicts(
        self,
        new_signal: Signal,
        conflicts: List[SignalConflict]
    ) -> Optional[Signal]:
        """Resolve conflicts between signals."""
        for conflict in conflicts:
            # Handle duplicate signals
            if conflict.conflict_type == "duplicate":
                # Keep the stronger signal
                if new_signal.strength > conflict.signal2.strength:
                    # Remove old signal
                    self._active_signals[new_signal.symbol].remove(conflict.signal2)
                    return new_signal
                else:
                    return None  # Reject new signal

            # Handle opposite signals
            elif conflict.conflict_type == "opposite_direction":
                # Apply resolution rules
                resolution = self._apply_resolution_rules(new_signal, conflict.signal2)

                if resolution == "keep_new":
                    # Remove conflicting signal
                    self._active_signals[new_signal.symbol].remove(conflict.signal2)
                    return new_signal
                elif resolution == "keep_existing":
                    return None
                elif resolution == "combine":
                    # Create a neutral signal or reduce position size
                    new_signal.strength = (new_signal.strength + conflict.signal2.strength) / 2
                    new_signal.signal_type = "NEUTRAL"
                    return new_signal

            # Handle conflicting targets
            elif conflict.conflict_type == "conflicting_targets":
                # Average the targets
                new_signal.price_target = (new_signal.price_target + conflict.signal2.price_target) / 2
                if new_signal.stop_loss and conflict.signal2.stop_loss:
                    new_signal.stop_loss = (new_signal.stop_loss + conflict.signal2.stop_loss) / 2
                if new_signal.take_profit and conflict.signal2.take_profit:
                    new_signal.take_profit = (new_signal.take_profit + conflict.signal2.take_profit) / 2

        return new_signal

    def _apply_resolution_rules(self, signal1: Signal, signal2: Signal) -> str:
        """Apply resolution rules for conflicting signals."""
        # Priority based on signal strength
        if abs(signal1.strength - signal2.strength) > 30:
            return "keep_new" if signal1.strength > signal2.strength else "keep_existing"

        # Priority based on signal type
        strong_signals = ["STRONG_BUY", "STRONG_SELL"]
        if signal1.signal_type in strong_signals and signal2.signal_type not in strong_signals:
            return "keep_new"
        elif signal2.signal_type in strong_signals and signal1.signal_type not in strong_signals:
            return "keep_existing"

        # If signals are close in strength, combine them
        if abs(signal1.strength - signal2.strength) < 10:
            return "combine"

        # Default to keeping the newer signal
        return "keep_new"

    def _calculate_priority(self, signal: Signal) -> SignalPriority:
        """Calculate signal priority."""
        # Apply priority rules
        for rule in self._priority_rules:
            if rule.matches(signal):
                return rule.priority

        # Default priority based on strength
        if signal.strength >= 80:
            return SignalPriority.HIGH
        elif signal.strength >= 60:
            return SignalPriority.MEDIUM
        else:
            return SignalPriority.LOW

    async def _persist_signal(self, signal: Signal, priority: SignalPriority) -> None:
        """Persist signal to database."""
        async with self.db.session() as session:
            # Prepare signal data
            signal_data = {
                'symbol': signal.symbol,
                'strategy_name': signal.strategy,
                'signal_type': signal.signal_type,
                'signal_strength': signal.strength,
                'price_target': signal.price_target,
                'stop_loss': signal.stop_loss,
                'take_profit': signal.take_profit,
                'reason': signal.metadata,
                'features': {
                    'priority': priority.value,
                    'timestamp': signal.timestamp.isoformat() if signal.timestamp else None
                },
                'executed': False
            }

            stmt = insert(TradingSignal).values(signal_data)
            await session.execute(stmt)
            await session.commit()

    async def _process_signal(self, signal: Signal) -> None:
        """Process signal for potential execution."""
        # This would trigger execution logic
        # For now, just log
        logger.debug(f"Processing signal: {signal}")

        # Check if signal should be executed immediately
        if signal.strength >= 80 and signal.signal_type in ["STRONG_BUY", "STRONG_SELL"]:
            logger.info(f"High priority signal ready for execution: {signal}")

    def _signal_to_dict(self, signal_record: TradingSignal) -> Dict[str, Any]:
        """Convert signal record to dictionary."""
        return {
            'id': signal_record.id,
            'symbol': signal_record.symbol,
            'strategy': signal_record.strategy_name,
            'signal_type': signal_record.signal_type,
            'strength': float(signal_record.signal_strength) if signal_record.signal_strength else 0,
            'price_target': float(signal_record.price_target) if signal_record.price_target else None,
            'stop_loss': float(signal_record.stop_loss) if signal_record.stop_loss else None,
            'take_profit': float(signal_record.take_profit) if signal_record.take_profit else None,
            'reason': signal_record.reason,
            'features': signal_record.features,
            'created_at': signal_record.created_at.isoformat() if signal_record.created_at else None,
            'executed': signal_record.executed,
            'order_id': signal_record.order_id
        }

    def _init_conflict_rules(self) -> List[ConflictRule]:
        """Initialize conflict resolution rules."""
        rules = []

        # Rule: Strong signals override weak signals
        rules.append(ConflictRule(
            name="strong_override",
            condition=lambda s1, s2: s1.strength > s2.strength + 20,
            resolution="keep_new"
        ))

        # Rule: Same strategy updates
        rules.append(ConflictRule(
            name="strategy_update",
            condition=lambda s1, s2: s1.strategy == s2.strategy,
            resolution="keep_new"
        ))

        return rules

    def _init_priority_rules(self) -> List[PriorityRule]:
        """Initialize priority calculation rules."""
        rules = []

        # Critical priority for very strong signals
        rules.append(PriorityRule(
            name="critical_strength",
            condition=lambda s: s.strength >= 90,
            priority=SignalPriority.CRITICAL
        ))

        # High priority for strong buy/sell
        rules.append(PriorityRule(
            name="strong_signals",
            condition=lambda s: s.signal_type in ["STRONG_BUY", "STRONG_SELL"],
            priority=SignalPriority.HIGH
        ))

        # High priority for breakout signals
        rules.append(PriorityRule(
            name="breakout_signals",
            condition=lambda s: "BREAKOUT" in s.signal_type,
            priority=SignalPriority.HIGH
        ))

        return rules

    async def mark_signal_executed(
        self,
        signal_id: int,
        order_id: str
    ) -> bool:
        """
        Mark a signal as executed.

        Args:
            signal_id: Signal ID
            order_id: Associated order ID

        Returns:
            True if successful
        """
        try:
            async with self.db.session() as session:
                stmt = update(TradingSignal).where(
                    TradingSignal.id == signal_id
                ).values(
                    executed=True,
                    order_id=order_id
                )

                await session.execute(stmt)
                await session.commit()

            return True

        except Exception as e:
            logger.error(f"Error marking signal as executed: {e}")
            return False

    async def get_signal_performance(
        self,
        strategy: Optional[str] = None,
        lookback_days: int = 30
    ) -> Dict[str, Any]:
        """
        Get signal performance statistics.

        Args:
            strategy: Filter by strategy
            lookback_days: Number of days to look back

        Returns:
            Performance statistics
        """
        cutoff_date = datetime.now() - timedelta(days=lookback_days)

        async with self.db.session() as session:
            stmt = select(TradingSignal).where(
                TradingSignal.created_at >= cutoff_date
            )

            if strategy:
                stmt = stmt.where(TradingSignal.strategy_name == strategy)

            result = await session.execute(stmt)
            signals = result.scalars().all()

            # Calculate statistics
            total_signals = len(signals)
            executed_signals = sum(1 for s in signals if s.executed)
            execution_rate = executed_signals / total_signals if total_signals > 0 else 0

            # Group by signal type
            signal_types = defaultdict(int)
            for s in signals:
                signal_types[s.signal_type] += 1

            # Average strength by type
            strength_by_type = defaultdict(list)
            for s in signals:
                if s.signal_strength:
                    strength_by_type[s.signal_type].append(float(s.signal_strength))

            avg_strength_by_type = {
                sig_type: sum(strengths) / len(strengths)
                for sig_type, strengths in strength_by_type.items()
                if strengths
            }

            return {
                'total_signals': total_signals,
                'executed_signals': executed_signals,
                'execution_rate': execution_rate,
                'signal_type_distribution': dict(signal_types),
                'average_strength_by_type': avg_strength_by_type,
                'period_days': lookback_days
            }


@dataclass
class ConflictRule:
    """Rule for resolving signal conflicts."""
    name: str
    condition: Any  # Callable that takes two signals
    resolution: str


@dataclass
class PriorityRule:
    """Rule for calculating signal priority."""
    name: str
    condition: Any  # Callable that takes a signal
    priority: SignalPriority