"""Tests for activity aggregation and unusual-activity detection."""

from __future__ import annotations

import random

import pytest

from custom_components.pihole_queries.activity import (
    BUCKET_SECONDS,
    DetectionParams,
    aggregate,
    score_series,
    statistics_period,
    validate_selection,
)

DAY = 86400
START = 1_735_689_600  # 2025-01-01T00:00:00Z


def _rows(rates: list[float], base: int = 300, start: int = START):
    """Build recorder rows from queries/min values."""
    return [
        {"start": start + i * base, "change": rate * base / 60}
        for i, rate in enumerate(rates)
    ]


def _series(rates: list[float], bucket: int = 300):
    return aggregate(_rows(rates, bucket), bucket, bucket)


def test_aggregate_sums_into_larger_bucket_and_normalises_rate() -> None:
    rows = _rows([6.0] * 6)  # six 5-minute rows of 6 queries/min
    series = aggregate(rows, 300, 1800)
    assert len(series) == 1
    assert series[0]["count"] == pytest.approx(180)
    assert series[0]["rate"] == pytest.approx(6.0)


def test_partial_bucket_is_not_a_drop() -> None:
    rows = _rows([10.0] * 4)  # only 20 of 30 minutes observed
    series = aggregate(rows, 300, 1800)
    assert series[0]["rate"] == pytest.approx(10.0)
    assert series[0]["covered"] == 1200


def test_counter_reset_and_missing_values_carry_no_volume() -> None:
    rows = [
        {"start": START, "change": -500.0},
        {"start": START + 300, "change": None},
        {"start": START + 600, "change": float("nan")},
    ]
    series = aggregate(rows, 300, 300)
    assert [p["count"] for p in series] == [0.0, 0.0]


def test_validate_selection() -> None:
    assert validate_selection("5m", "1d") is None
    assert validate_selection("1h", "30d") is None
    assert validate_selection("5m", "30d") == "fine_buckets_unavailable"
    assert validate_selection("1d", "6h") == "bucket_larger_than_span"
    assert validate_selection("2m", "1d") == "unknown_option"


def test_statistics_period_choice() -> None:
    assert statistics_period(BUCKET_SECONDS["30m"]) == "5minute"
    assert statistics_period(BUCKET_SECONDS["3h"]) == "hour"


def _noisy_week(seed: int = 1, level: float = 20.0, days: int = 7):
    rng = random.Random(seed)
    return [max(0.0, level + rng.uniform(-4, 4)) for _ in range(days * 288)]


def test_stable_device_has_no_unusual_runs() -> None:
    series = _series(_noisy_week())
    result = score_series(series, 300, visible_from=START + 6 * DAY)
    assert result["has_baseline"] is True
    assert result["runs"] == []
    assert result["score"] < 1.0


def test_sustained_spike_is_flagged() -> None:
    rates = _noisy_week()
    for i in range(len(rates) - 12, len(rates)):  # last hour
        rates[i] = 300.0
    result = score_series(_series(rates), 300, visible_from=START + 6 * DAY)
    assert len(result["runs"]) == 1
    assert result["runs"][0]["peak_rate"] == pytest.approx(300.0)
    assert result["score"] > 2
    assert result["latest"]["unusual"] is True


def test_single_bucket_blip_is_ignored() -> None:
    rates = _noisy_week()
    rates[-5] = 400.0
    result = score_series(_series(rates), 300, visible_from=START + 6 * DAY)
    assert result["runs"] == []


def test_quiet_device_needs_absolute_minimum() -> None:
    rates = [0.1] * (7 * 288)
    for i in range(len(rates) - 6, len(rates)):
        rates[i] = 2.0  # 20x its median, but still almost nothing
    result = score_series(_series(rates), 300, visible_from=START + 6 * DAY)
    assert result["runs"] == []


def test_quiet_device_that_really_explodes_is_flagged() -> None:
    rates = [0.1] * (7 * 288)
    for i in range(len(rates) - 6, len(rates)):
        rates[i] = 120.0
    result = score_series(_series(rates), 300, visible_from=START + 6 * DAY)
    assert len(result["runs"]) == 1


def test_little_history_gives_no_baseline() -> None:
    result = score_series(_series([50.0] * 10), 300, visible_from=START)
    assert result["has_baseline"] is False
    assert result["runs"] == []
    assert len(result["points"]) == 10


def test_daily_pattern_is_not_flagged() -> None:
    # Busy 18-22h UTC every day: normal for this device.
    rates = []
    for slot in range(7 * 288):
        hour = slot // 12 % 24
        rates.append(80.0 if 18 <= hour < 22 else 5.0)
    result = score_series(_series(rates), 300, visible_from=START + 3 * DAY)
    assert result["runs"] == []


def test_gap_splits_runs() -> None:
    rates = _noisy_week()
    series = _series(rates)
    for p in series[-12:]:
        p["rate"] = 300.0
    del series[-8]  # hole inside the spike
    result = score_series(series, 300, visible_from=START + 6 * DAY)
    assert len(result["runs"]) == 2


def test_moderate_rise_is_ignored_by_default_but_catchable() -> None:
    rates = _noisy_week()
    for i in range(len(rates) - 12, len(rates)):
        rates[i] = 60.0  # 3x its usual level: busy, not crazy
    series = _series(rates)
    assert score_series(series, 300, START + 6 * DAY)["runs"] == []
    sensitive = DetectionParams(deviations=4, ratio=2, min_rate=5, min_run_seconds=600)
    assert len(score_series(series, 300, START + 6 * DAY, sensitive)["runs"]) == 1


def test_run_reports_multiple_of_usual() -> None:
    rates = _noisy_week()
    for i in range(len(rates) - 12, len(rates)):
        rates[i] = 300.0
    run = score_series(_series(rates), 300, START + 6 * DAY)["runs"][0]
    assert 10 < run["peak_multiple"] < 20


def test_regularly_bursty_device_is_not_flagged() -> None:
    # Quiet device that bursts to 60/min every 3 hours, every day.
    rates = [1.0] * (7 * 288)
    for slot in range(len(rates)):
        if slot % 36 < 4:
            rates[slot] = 60.0
    result = score_series(_series(rates), 300, visible_from=START + 3 * DAY)
    assert result["runs"] == []


def test_one_bad_day_does_not_hide_the_next_spike() -> None:
    rates = _noisy_week()
    for i in range(288 * 3, 288 * 3 + 144):  # twelve hours of 300/min on day 4
        rates[i] = 300.0
    for i in range(len(rates) - 12, len(rates)):
        rates[i] = 300.0
    result = score_series(_series(rates), 300, visible_from=START + 6 * DAY)
    assert len(result["runs"]) == 1
