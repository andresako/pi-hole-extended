"""Regression checks for bounded per-client query history updates."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from custom_components.pihole_queries import coordinator as coordinator_module
from custom_components.pihole_queries.coordinator import PiHoleQueryCoordinator


def _coordinator(recent_queries: list[dict[str, Any]], *, last_query_id: int = 0):
    coordinator = object.__new__(PiHoleQueryCoordinator)
    coordinator.client_data = {
        "192.0.2.10": {
            "client_name": "fixture-client",
            "client_ip": "192.0.2.10",
            "query_count": 4,
            "last_query_id": last_query_id,
            "recent_queries": recent_queries,
            "recent_activity": [],
            "classifier_counts": {"1": {"allowed": 0, "blocked": 0, "provisional": 0}},
        }
    }
    return coordinator


def _row(query_id: int) -> dict[str, Any]:
    return {
        "id": query_id,
        "time": 1_735_689_600,
        "domain": f"q{query_id}.example.test",
        "client": {"ip": "192.0.2.10", "name": "fixture-client"},
        "status": "FORWARDED",
    }


def _item(query_id: Any, domain: str | None = None) -> dict[str, Any]:
    return {"id": query_id, "domain": domain or f"old-{query_id}.example.test"}


def test_append_only_history_stays_ordered_and_reuses_details_without_aliasing(
    monkeypatch,
) -> None:
    coordinator = _coordinator([_item(101), _item(102)], last_query_id=102)
    original_summary = coordinator_module._query_summary
    calls = 0

    def counted_summary(query, query_id):
        nonlocal calls
        calls += 1
        return original_summary(query, query_id)

    monkeypatch.setattr(coordinator_module, "_query_summary", counted_summary)
    coordinator._update_client(_row(103), 103, count_as_new=True)

    summary = coordinator.client_data["192.0.2.10"]
    recent = summary["recent_queries"]
    assert [item["id"] for item in recent] == [101, 102, 103]
    assert summary["last_query"]["id"] == 103
    assert summary["last_query"] == recent[-1]
    assert summary["last_query"] is not recent[-1]
    summary["last_query"]["domain"] = "changed.example.test"
    assert recent[-1]["domain"] == "q103.example.test"
    assert summary["query_count"] == 5
    assert summary["classifier_counts"]["1"]["allowed"] == 1
    assert calls == 1


def test_migrated_nonmonotonic_history_keeps_legacy_sort_order() -> None:
    coordinator = _coordinator(
        [_item("12"), _item(10), _item(99), _item(None), _item("11")]
    )

    coordinator._update_client(_row(13), 13, count_as_new=True)

    recent = coordinator.client_data["192.0.2.10"]["recent_queries"]
    assert [item["id"] for item in recent] == [None, 10, "11", "12", 13, 99]


def test_duplicate_recent_id_is_not_appended_and_latest_query_still_updates() -> None:
    coordinator = _coordinator(
        [_item(104, "migrated.example.test"), _item(102)], last_query_id=100
    )

    coordinator._update_client(_row(104), 104, count_as_new=True)

    summary = coordinator.client_data["192.0.2.10"]
    assert [item["id"] for item in summary["recent_queries"]] == [102, 104]
    assert summary["recent_queries"][-1]["domain"] == "migrated.example.test"
    assert summary["last_query_id"] == 104
    assert summary["last_query"]["domain"] == "q104.example.test"


def test_older_row_does_not_replace_last_query() -> None:
    coordinator = _coordinator([_item(101)], last_query_id=105)
    summary = coordinator.client_data["192.0.2.10"]
    summary["last_query"] = {"id": 105, "domain": "newest.example.test"}

    coordinator._update_client(_row(103), 103, count_as_new=True)

    assert summary["last_query_id"] == 105
    assert summary["last_query"] == {"id": 105, "domain": "newest.example.test"}


def test_older_row_below_full_history_does_not_rebuild_or_sort_history(monkeypatch) -> None:
    recent = [_item(query_id) for query_id in range(101, 111)]
    coordinator = _coordinator(recent, last_query_id=110)
    summary = coordinator.client_data["192.0.2.10"]
    summary["last_query"] = {"id": 110, "domain": "newest.example.test"}
    calls = 0

    def unexpected_summary(_query, _query_id):
        nonlocal calls
        calls += 1
        raise AssertionError("an older row below the retained window has no detail to add")

    monkeypatch.setattr(coordinator_module, "_query_summary", unexpected_summary)

    coordinator._update_client(_row(50), 50, count_as_new=True)

    assert [item["id"] for item in summary["recent_queries"]] == list(range(101, 111))
    assert summary["last_query"] == {"id": 110, "domain": "newest.example.test"}
    assert summary["query_count"] == 5
    assert calls == 0


def test_observer_snapshot_omits_only_the_private_activity_journal() -> None:
    coordinator = _coordinator([_item(101)])
    summary = coordinator.client_data["192.0.2.10"]
    summary["recent_activity"] = [
        {
            "observed_at": 1_735_689_600.0,
            "classifier_version": 1,
            "classification": "allowed",
        }
    ]
    summary["last_query"] = {"id": 101, "domain": "observed.example.test"}

    snapshot = coordinator._data_snapshot()["192.0.2.10"]

    assert "recent_activity" not in snapshot
    assert coordinator.client_data["192.0.2.10"]["recent_activity"] == [
        {
            "observed_at": 1_735_689_600.0,
            "classifier_version": 1,
            "classification": "allowed",
        }
    ]
    snapshot["last_query"]["domain"] = "changed.example.test"
    assert summary["last_query"]["domain"] == "observed.example.test"
