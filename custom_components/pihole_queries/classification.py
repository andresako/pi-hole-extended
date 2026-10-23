"""Versioned, literal Pi-hole query status classification."""

from __future__ import annotations

from typing import Any


CLASSIFIER_VERSION = 1

STATUS_CLASSIFIERS: dict[int, dict[str, str]] = {
    1: {
        **dict.fromkeys(
            (
                "FORWARDED",
                "CACHE",
                "RETRIED",
                "RETRIED_DNSSEC",
                "CACHE_STALE",
            ),
            "allowed",
        ),
        **dict.fromkeys(
            (
                "GRAVITY",
                "REGEX",
                "DENYLIST",
                "EXTERNAL_BLOCKED_IP",
                "EXTERNAL_BLOCKED_NULL",
                "EXTERNAL_BLOCKED_NXRA",
                "GRAVITY_CNAME",
                "REGEX_CNAME",
                "DENYLIST_CNAME",
                "DBBUSY",
                "SPECIAL_DOMAIN",
                "EXTERNAL_BLOCKED_EDE15",
            ),
            "blocked",
        ),
        "UNKNOWN": "provisional",
        "IN_PROGRESS": "provisional",
    }
}


def classify_status(status: Any, version: int = CLASSIFIER_VERSION) -> str:
    """Return the contracted class; unknown and malformed values stay provisional."""
    if not isinstance(status, str):
        return "provisional"
    return STATUS_CLASSIFIERS.get(version, {}).get(status, "provisional")


def empty_classifier_counts() -> dict[str, int]:
    """Return zeroed counters for one immutable classifier epoch."""
    return {"allowed": 0, "blocked": 0, "provisional": 0}
