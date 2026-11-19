"""Tests for the remaining print time estimation."""

from __future__ import annotations

import pytest

from custom_components.flashforge.estimator import PrintTimeEstimator

PRINTING = "BUILDING_FROM_SD"
PAUSED = "PAUSED"
READY = "READY"

# Print at 1 % per minute: 94 % left after 6 % is 94 minutes.
REMAINING_AT_6_PERCENT = 94 * 60


def feed(
    estimator: PrintTimeEstimator,
    samples: list[tuple[float, str, float]],
) -> list[float | None]:
    """Feed (time, status, progress) samples, return the estimates."""
    remaining = []
    for now, status, progress in samples:
        estimator.update(status=status, progress=progress, now=now)
        remaining.append(estimator.remaining)
    return remaining


def printing(
    start: float, end: float, first_step_at: float
) -> list[tuple[float, str, float]]:
    """Sample every 30 s, 1 % per minute starting at first_step_at."""
    samples = []
    now = start
    while now <= end:
        progress = (now - first_step_at) // 60 + 1 if now >= first_step_at else 0
        samples.append((now, PRINTING, progress))
        now += 30
    return samples


def test_no_estimate_below_minimum_progress() -> None:
    """Test that nothing is estimated before 5 % progress was observed."""
    estimator = PrintTimeEstimator()
    # Heating up for 5 minutes, then 1 % per minute. First step to 1 % at 300 s.
    remaining = feed(estimator, printing(0, 570, first_step_at=300))

    assert estimator.remaining is None
    assert set(remaining) == {None}

    # 6 % reached at 600 s: 5 % progress since the first step.
    estimator.update(status=PRINTING, progress=6, now=600)
    assert estimator.remaining == pytest.approx(REMAINING_AT_6_PERCENT)


def test_heating_time_is_ignored() -> None:
    """Test that time before the first progress step doesn't slow the rate."""
    estimator = PrintTimeEstimator()
    feed(estimator, printing(0, 600 + 1800, first_step_at=300 + 1800))

    assert estimator.remaining == pytest.approx(REMAINING_AT_6_PERCENT)


def test_remaining_counts_down_between_steps() -> None:
    """Test that the estimate decreases while the progress doesn't change."""
    estimator = PrintTimeEstimator()
    feed(estimator, printing(0, 600, first_step_at=300))
    estimator.update(status=PRINTING, progress=6, now=630)

    assert estimator.remaining == pytest.approx(REMAINING_AT_6_PERCENT - 30)


def test_pause_is_ignored() -> None:
    """Test that a pause doesn't slow the rate and gives no estimate."""
    estimator = PrintTimeEstimator()
    feed(estimator, printing(0, 600, first_step_at=300))

    # Paused for 10 minutes.
    paused = feed(estimator, [(630 + 30 * i, PAUSED, 6) for i in range(20)])
    assert set(paused) == {None}

    # Continue printing, next step to 7 % one minute of printing after 6 %.
    estimator.update(status=PRINTING, progress=6, now=1230)
    estimator.update(status=PRINTING, progress=7, now=1260)

    assert estimator.remaining == pytest.approx(93 * 60)


def test_new_print_resets_estimate() -> None:
    """Test that a new print (progress going down) starts over."""
    estimator = PrintTimeEstimator()
    feed(estimator, printing(0, 600, first_step_at=300))
    assert estimator.remaining is not None

    estimator.update(status=PRINTING, progress=0, now=630)
    assert estimator.remaining is None

    # The old print must not influence the new one.
    feed(estimator, printing(660, 660 + 600, first_step_at=660 + 300))
    assert estimator.remaining == pytest.approx(REMAINING_AT_6_PERCENT)


def test_ready_resets_estimate() -> None:
    """Test that no estimate is given when the printer is idle."""
    estimator = PrintTimeEstimator()
    feed(estimator, printing(0, 600, first_step_at=300))

    estimator.update(status=READY, progress=6, now=630)
    assert estimator.remaining is None

    # Starts over, so it needs 5 % progress again.
    estimator.update(status=PRINTING, progress=6, now=660)
    estimator.update(status=PRINTING, progress=7, now=720)
    assert estimator.remaining is None


def test_started_during_print() -> None:
    """Test Home Assistant starting while the printer is at 40 %."""
    estimator = PrintTimeEstimator()
    samples = [(60.0 * i, PRINTING, 40.0 + i) for i in range(7)]
    remaining = feed(estimator, samples)

    # First step at 41 %, so the first estimate is at 46 %.
    assert remaining[:6] == [None] * 6
    assert remaining[6] == pytest.approx(54 * 60)
