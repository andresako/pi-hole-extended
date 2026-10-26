"""Shared validators for persisted counters and buckets."""

from __future__ import annotations

from typing import Any


def valid_query_time_buckets(value: Any) -> bool:
    """Validate bounded, anonymous per-second source-time totals."""
    if not isinstance(value, list) or len(value) > 61:
        return False
    seen: set[int] = set()
    for bucket in value:
        if (
            not isinstance(bucket, dict)
            or set(bucket) != {"second", "count"}
            or isinstance(bucket.get("second"), bool)
            or not isinstance(bucket.get("second"), int)
            or bucket["second"] < 0
            or isinstance(bucket.get("count"), bool)
            or not isinstance(bucket.get("count"), int)
            or bucket["count"] < 0
            or bucket["second"] in seen
        ):
            return False
        seen.add(bucket["second"])
    return True


def valid_classifier_epoch(epoch: Any) -> bool:
    """Accept only canonical, bounded numeric classifier versions."""
    if (
        not isinstance(epoch, str)
        or not 1 <= len(epoch) <= 10
        or not epoch.isascii()
        or not epoch.isdecimal()
    ):
        return False
    version = int(epoch)
    return 1 <= version <= 2**31 - 1 and str(version) == epoch
