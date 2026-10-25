"""Per-client activity series and robust spike detection.

Pure functions only: the websocket layer feeds them recorder statistics and the
card renders the result. Only aggregate counts are handled, never domains.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

BUCKET_SECONDS: dict[str, int] = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "3h": 10800,
    "6h": 21600,
    "1d": 86400,
}
SPAN_SECONDS: dict[str, int] = {
    "6h": 6 * 3600,
    "12h": 12 * 3600,
    "1d": 86400,
    "3d": 3 * 86400,
    "7d": 7 * 86400,
    "14d": 14 * 86400,
    "30d": 30 * 86400,
}
# Recorder keeps 5-minute statistics for about ten days by default.
FINE_STATISTICS_SECONDS = 10 * 86400
BASELINE_LOOKBACK_SECONDS = 7 * 86400
MAX_BUCKETS = 1500

MAD_TO_SIGMA = 1.4826
MIN_BASELINE_POINTS = 24
MIN_SEASONAL_POINTS = 12
MIN_DAY_COVERAGE_SECONDS = 3 * 3600


@dataclass(frozen=True)
class DetectionParams:
    """Thresholds for flagging unusual activity."""

    deviations: float = 8.0
    ratio: float = 5.0
    min_rate: float = 10.0
    min_run_seconds: int = 900
    peak_factor: float = 1.5


SENSITIVITY_PARAMS: dict[str, DetectionParams] = {
    "low": DetectionParams(
        deviations=10.0, ratio=8.0, min_rate=20.0, min_run_seconds=1800, peak_factor=2.0
    ),
    "medium": DetectionParams(),
    "high": DetectionParams(
        deviations=6.0, ratio=3.0, min_rate=5.0, min_run_seconds=600, peak_factor=1.2
    ),
}


def params_for(level: object) -> DetectionParams:
    """Map a sensitivity option to thresholds; unknown values mean medium."""
    if isinstance(level, str):
        return SENSITIVITY_PARAMS.get(level, SENSITIVITY_PARAMS["medium"])
    return SENSITIVITY_PARAMS["medium"]


def validate_selection(bucket: str, span: str) -> str | None:
    """Return why a bucket/span pair cannot be served, or None when valid."""
    if bucket not in BUCKET_SECONDS or span not in SPAN_SECONDS:
        return "unknown_option"
    bucket_seconds = BUCKET_SECONDS[bucket]
    span_seconds = SPAN_SECONDS[span]
    if bucket_seconds > span_seconds:
        return "bucket_larger_than_span"
    if bucket_seconds < 3600 and span_seconds > FINE_STATISTICS_SECONDS:
        return "fine_buckets_unavailable"
    if span_seconds // bucket_seconds > MAX_BUCKETS:
        return "too_many_buckets"
    return None


def statistics_period(bucket_seconds: int) -> str:
    """Pick the recorder resolution able to build the requested bucket."""
    return "5minute" if bucket_seconds < 3600 else "hour"


def aggregate(
    rows: Iterable[Mapping[str, Any]],
    base_seconds: int,
    bucket_seconds: int,
) -> list[dict[str, float]]:
    """Sum recorder `change` rows into buckets and normalise to queries/min.

    Rows are ``{"start": epoch_seconds, "change": number | None}``. The rate is
    divided by the time actually covered, so a partially filled bucket or a
    recorder gap does not look like a drop in activity.
    """
    counts: dict[int, float] = defaultdict(float)
    covered: dict[int, int] = defaultdict(int)
    for row in rows:
        change = row.get("change")
        start = row.get("start")
        if change is None or start is None:
            continue
        if not math.isfinite(change) or change < 0:
            # Counter resets and negative corrections carry no volume.
            change = 0.0
        key = int(start // bucket_seconds * bucket_seconds)
        counts[key] += change
        covered[key] += base_seconds
    series = []
    for key in sorted(counts):
        seconds = min(covered[key], bucket_seconds)
        series.append(
            {
                "t": key,
                "count": counts[key],
                "rate": counts[key] * 60.0 / seconds if seconds else 0.0,
                "covered": seconds,
            }
        )
    return series


def _profile(values: Sequence[float]) -> tuple[float, float]:
    """Return median and MAD of a device's own rates."""
    mid = median(values)
    return mid, median(abs(v - mid) for v in values)


def _typical_daily_peak(
    points: Sequence[Mapping[str, float]], bucket_seconds: int
) -> float:
    """Median of the daily maximum rate; 0 when fewer than three usable days.

    A device that bursts every day has a high typical peak, so its regular bursts
    are not flagged. A single bad day cannot move a median of several days.
    """
    daily: dict[int, list[float]] = defaultdict(list)
    for point in points:
        daily[int(point["t"] // 86400)].append(point["rate"])
    peaks = [
        max(rates)
        for rates in daily.values()
        if len(rates) * bucket_seconds >= MIN_DAY_COVERAGE_SECONDS
    ]
    return median(peaks) if len(peaks) >= 3 else 0.0


def _baselines(
    points: Sequence[Mapping[str, float]], bucket_seconds: int
) -> tuple[tuple[float, float], dict[int, tuple[float, float]]]:
    global_stats = _profile([p["rate"] for p in points])
    seasonal: dict[int, tuple[float, float]] = {}
    if bucket_seconds <= 3600:
        by_hour: dict[int, list[float]] = defaultdict(list)
        for point in points:
            by_hour[int(point["t"] // 3600 % 24)].append(point["rate"])
        # Seasonality needs several days; otherwise one global profile is safer.
        spans_days = (points[-1]["t"] - points[0]["t"]) >= 3 * 86400
        if spans_days:
            for hour, values in by_hour.items():
                if len(values) >= MIN_SEASONAL_POINTS:
                    seasonal[hour] = _profile(values)
    return global_stats, seasonal


def score_series(
    points: Sequence[Mapping[str, float]],
    bucket_seconds: int,
    visible_from: float,
    params: DetectionParams = DetectionParams(),
) -> dict[str, Any]:
    """Annotate a series with baseline, threshold and unusual runs.

    The baseline comes from every point supplied (including the lookback before
    ``visible_from``); only points from ``visible_from`` are returned.
    """
    if len(points) < MIN_BASELINE_POINTS:
        return {
            "has_baseline": False,
            "points": [
                {**p, "base": None, "threshold": None, "unusual": False}
                for p in points
                if p["t"] >= visible_from
            ],
            "runs": [],
            "score": 0.0,
            "latest": None,
        }

    global_stats, seasonal = _baselines(points, bucket_seconds)
    typical_peak = _typical_daily_peak(points, bucket_seconds)
    min_points = max(1, math.ceil(params.min_run_seconds / bucket_seconds))

    annotated = []
    for point in points:
        mid, mad = seasonal.get(int(point["t"] // 3600 % 24), global_stats)
        threshold = max(
            mid + params.deviations * MAD_TO_SIGMA * mad,
            mid * params.ratio,
            typical_peak * params.peak_factor,
            params.min_rate,
        )
        annotated.append(
            {
                **point,
                "base": mid,
                "threshold": threshold,
                "over": point["rate"] > threshold,
            }
        )

    runs: list[dict[str, float]] = []
    index = 0
    while index < len(annotated):
        if not annotated[index]["over"]:
            index += 1
            continue
        end = index
        while (
            end + 1 < len(annotated)
            and annotated[end + 1]["over"]
            and annotated[end + 1]["t"] - annotated[end]["t"] <= bucket_seconds
        ):
            end += 1
        if end - index + 1 >= min_points:
            for item in annotated[index : end + 1]:
                item["unusual"] = True
            peak_index = max(
                range(index, end + 1),
                key=lambda i: annotated[i]["rate"] / annotated[i]["threshold"],
            )
            peak = annotated[peak_index]["rate"] / annotated[peak_index]["threshold"]
            peak_base = annotated[peak_index]["base"]
            runs.append(
                {
                    "start": annotated[index]["t"],
                    "end": annotated[end]["t"] + bucket_seconds,
                    "peak_ratio": peak,
                    "peak_rate": annotated[peak_index]["rate"],
                    "peak_multiple": (
                        annotated[peak_index]["rate"] / peak_base if peak_base > 0 else None
                    ),
                }
            )
        index = end + 1

    visible = [
        {
            "t": p["t"],
            "count": p["count"],
            "rate": p["rate"],
            "base": p["base"],
            "threshold": p["threshold"],
            "unusual": bool(p.get("unusual", False)),
        }
        for p in annotated
        if p["t"] >= visible_from
    ]
    visible_runs = [r for r in runs if r["end"] > visible_from]
    score = max((r["peak_ratio"] for r in visible_runs), default=0.0)
    if not visible_runs and visible:
        score = max(v["rate"] / v["threshold"] for v in visible)
    return {
        "has_baseline": True,
        "points": visible,
        "runs": visible_runs,
        "score": score,
        "latest": visible[-1] if visible else None,
    }
