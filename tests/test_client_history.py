"""Regression checks that client summaries contain totals, not query rows."""

from __future__ import annotations

from typing import Any

from custom_components.pihole_queries.coordinator import PiHoleQueryCoordinator


def _coordinator() -> PiHoleQueryCoordinator:
    coordinator = object.__new__(PiHoleQueryCoordinator)
    coordinator.client_data = {
        "192.0.2.10": {
            "client_name": "test-client",
            "client_ip": "192.0.2.10",
            "query_count": 0,
        }
    }
    coordinator.retention_mode = "detail"
    return coordinator


def _row(query_id: int, domain: str = "private.example.test") -> dict[str, Any]:
    return {
        "id": query_id,
        "time": 1_735_689_600,
        "domain": domain,
        "client": {"ip": "192.0.2.10", "name": "test-client"},
        "status": "FORWARDED",
    }


def test_updates_keep_numeric_totals_and_bounded_buckets_only() -> None:
    coordinator = _coordinator()
    for query_id in range(1, 1001):
        coordinator._update_client(_row(query_id), query_id, count_as_new=True)

    summary = coordinator.client_data["192.0.2.10"]
    assert summary["query_count"] == 1000
    assert len(summary["activity_buckets"]) <= 61
    assert len(summary["query_time_buckets"]) <= 61
    assert "last_query" not in summary
    assert "last_query_id" not in summary
    assert "recent_queries" not in summary
    assert "recent_activity" not in summary
    assert "private.example.test" not in repr(summary)
