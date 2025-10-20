"""Simple progress tracking helper with structured logging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from time import perf_counter
from typing import Optional

from loguru import logger


def _format_duration(seconds: float) -> str:
    """Return human readable duration for logging."""
    if seconds < 0:
        seconds = 0
    return str(timedelta(seconds=round(seconds)))


@dataclass
class ProgressSnapshot:
    """Snapshot representing current progress state."""

    completed: int
    total: int
    succeeded: int
    failed: int
    processed_units: int
    elapsed_seconds: float

    @property
    def percent(self) -> float:
        return (self.completed / self.total) * 100 if self.total else 0.0

    @property
    def rate_per_second(self) -> float:
        if self.elapsed_seconds <= 0:
            return 0.0
        return self.processed_units / self.elapsed_seconds


class ProgressTracker:
    """Track per-item progress and log consistent updates."""

    def __init__(
        self,
        task_name: str,
        total_steps: int,
        unit_label: str = "records",
    ) -> None:
        if total_steps <= 0:
            raise ValueError("total_steps must be positive")

        self.task_name = task_name
        self.total_steps = total_steps
        self.unit_label = unit_label

        self._start = perf_counter()
        self._completed = 0
        self._succeeded = 0
        self._failed = 0
        self._processed_units = 0

    def _snapshot(self) -> ProgressSnapshot:
        return ProgressSnapshot(
            completed=self._completed,
            total=self.total_steps,
            succeeded=self._succeeded,
            failed=self._failed,
            processed_units=self._processed_units,
            elapsed_seconds=perf_counter() - self._start,
        )

    def record_success(
        self,
        item_label: str,
        processed_units: int = 0,
        message: Optional[str] = None,
    ) -> None:
        """Record a successful step and log progress."""
        self._completed += 1
        self._succeeded += 1
        if processed_units > 0:
            self._processed_units += processed_units
        snapshot = self._snapshot()

        log_message = (
            f"[{self.task_name}] {item_label} succeeded | "
            f"{snapshot.percent:.1f}% ({snapshot.completed}/{snapshot.total})"
        )
        if processed_units:
            log_message += f" | {processed_units} {self.unit_label}"
        if message:
            log_message += f" | {message}"

        log_message += (
            f" | elapsed { _format_duration(snapshot.elapsed_seconds) }"
        )
        if snapshot.processed_units and snapshot.rate_per_second:
            log_message += (
                f" | avg {snapshot.rate_per_second:.1f} {self.unit_label}/s"
            )

        logger.info(log_message)

    def record_failure(self, item_label: str, error: Optional[str] = None) -> None:
        """Record a failed step and log progress."""
        self._completed += 1
        self._failed += 1
        snapshot = self._snapshot()

        log_message = (
            f"[{self.task_name}] {item_label} failed | "
            f"{snapshot.percent:.1f}% ({snapshot.completed}/{snapshot.total})"
        )
        if error:
            log_message += f" | {error}"
        log_message += f" | elapsed { _format_duration(snapshot.elapsed_seconds) }"

        logger.warning(log_message)

    def log_summary(self) -> None:
        """Log final summary for the tracked task."""
        snapshot = self._snapshot()
        logger.info(
            f"[{self.task_name}] Completed {snapshot.completed}/{snapshot.total} items: "
            f"{snapshot.succeeded} succeeded, {snapshot.failed} failed, "
            f"processed {snapshot.processed_units} {self.unit_label} in "
            f"{_format_duration(snapshot.elapsed_seconds)}"
        )

