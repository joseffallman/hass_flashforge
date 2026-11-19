"""Estimate the remaining print time, the printers don't report it."""

from __future__ import annotations

STATUS_PRINTING = "BUILDING_FROM_SD"
STATUS_READY = "READY"
# Progress (percentage points) that must be observed before estimating.
MIN_PROGRESS_FOR_ESTIMATE = 5


class PrintTimeEstimator:
    """
    Estimate the remaining print time from the print progress.

    The progress rate is measured between the first and the latest observed
    progress step of the print. Time not spent printing (heating up before the
    first step, paused) doesn't count. No estimate is made until the progress
    advanced at least MIN_PROGRESS_FOR_ESTIMATE percentage points.
    """

    def __init__(self) -> None:
        """Initialize."""
        self._reset()

    def _reset(self) -> None:
        self._printing_time = 0.0
        self._last_time: float | None = None
        self._last_progress: float | None = None
        self._last_printing = False
        # (progress, printing time) when the progress changed.
        self._first_step: tuple[float, float] | None = None
        self._last_step: tuple[float, float] | None = None
        self.remaining: float | None = None

    def update(self, *, status: str | None, progress: float | None, now: float) -> None:
        """Add a sample, now is a monotonic time in seconds."""
        if (
            status == STATUS_READY
            or progress is None
            or (self._last_progress is not None and progress < self._last_progress)
        ):
            # Idle or a new print started.
            self._reset()

        if self._last_printing and self._last_time is not None:
            # It was printing at the last sample, count the time since then.
            self._printing_time += now - self._last_time

        printing = status == STATUS_PRINTING and progress is not None

        if (
            printing
            and self._last_progress is not None
            and progress > self._last_progress
        ):
            step = (progress, self._printing_time)
            if self._first_step is None:
                self._first_step = step
            self._last_step = step

        self._last_time = now
        self._last_progress = progress
        self._last_printing = printing
        self.remaining = self._estimate() if printing else None

    def _estimate(self) -> float | None:
        if self._first_step is None or self._last_step is None:
            return None
        progress = self._last_step[0] - self._first_step[0]
        duration = self._last_step[1] - self._first_step[1]
        if progress < MIN_PROGRESS_FOR_ESTIMATE or duration <= 0:
            return None
        remaining_after_step = (100 - self._last_step[0]) * duration / progress
        since_step = self._printing_time - self._last_step[1]
        return max(0.0, remaining_after_step - since_step)
