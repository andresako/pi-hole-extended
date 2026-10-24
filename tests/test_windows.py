"""Tests for HA-ingestion windows, timestamp validation, and persisted epochs."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from datetime import UTC, datetime
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.sensor import SensorStateClass

from custom_components.pihole_queries.coordinator import (
    PiHoleQueryCoordinator,
    _query_summary,
)
from custom_components.pihole_queries.sensor import PiHoleClientSensor, SENSOR_DESCRIPTIONS
from homeassistant.util import dt as dt_util


def _coordinator_with_client(client_data: dict[str, Any]) -> PiHoleQueryCoordinator:
    coordinator = object.__new__(PiHoleQueryCoordinator)
    coordinator.client_data = {"192.0.2.10": client_data}
    return coordinator


def test_window_uses_inclusive_ha_observation_time_and_rejects_bad_samples() -> None:
    now = 1_735_689_600.0
    coordinator = _coordinator_with_client(
        {
            "classifier_version": 1,
            "recent_activity": [
                {"observed_at": now - 60, "classifier_version": 1, "classification": "allowed"},
                {"observed_at": now - 60.001, "classifier_version": 1, "classification": "blocked"},
                {"observed_at": now, "classifier_version": 1, "classification": "blocked"},
                {"observed_at": now + 0.001, "classifier_version": 1, "classification": "provisional"},
                {"observed_at": "not-a-time", "classifier_version": 1, "classification": "allowed"},
                {"timestamp": now, "blocked": False},  # legacy origin-time record
                {"observed_at": now - 5, "classifier_version": 1, "classification": "unknown"},
                {"observed_at": now - 5, "classifier_version": 2, "classification": "allowed"},
            ],
        }
    )

    coordinator._update_minute_query_counts(now)
    summary = coordinator.client_data["192.0.2.10"]

    assert summary["queries_observed_last_minute"] == 3
    assert summary["classifier_rates"]["1"] == {
        "allowed": 1,
        "blocked": 1,
        "provisional": 0,
    }
    assert len(summary["recent_activity"]) == 3  # current v1 + current v2 sample


@pytest.mark.parametrize("origin_time", [None, "invalid", float("inf"), float("nan"), 1_735_689_661])
def test_origin_time_does_not_change_ingestion_window(origin_time: Any, frozen_time) -> None:
    now = 1_735_689_600.0
    row = {
        "id": 1,
        "domain": "late.example.test",
        "client": {"ip": "192.0.2.10", "name": "test-client"},
        "status": "FORWARDED",
        "time": origin_time,
    }
    coordinator = _coordinator_with_client(
        {
            "client_name": "test-client",
            "client_ip": "192.0.2.10",
            "query_count": 0,
            "allowed_count": 0,
            "blocked_count": 0,
            "last_query_id": 0,
            "recent_queries": [],
            "recent_activity": [],
        }
    )

    coordinator._update_client(row, 1, count_as_new=True)
    coordinator._update_minute_query_counts(now)
    summary = coordinator.client_data["192.0.2.10"]

    assert summary["query_count"] == 1
    assert summary["classifier_counts"]["1"]["allowed"] == 1
    assert summary["queries_observed_last_minute"] == 1
    assert summary["recent_queries"][-1]["time"] is None


@pytest.mark.parametrize("source_time", [None, "bad", 1_735_689_661])
def test_unreliable_source_time_is_not_fabricated_as_current(source_time: Any, frozen_time) -> None:
    summary = _query_summary(
        {"id": 1, "time": source_time, "status": None, "domain": "x.example.test"}, 1
    )

    assert summary["time"] is None
    assert summary["status"] is None
    assert summary["result"] == "provisional"


@pytest.mark.parametrize(
    ("observed_at", "source_times", "expected_local_times"),
    [
        (
            "2025-01-02T00:00:30+00:00",
            ("2025-01-01T23:59:50+00:00", "2025-01-02T00:00:10+00:00"),
            ("2025-01-02 00:59:50+01:00", "2025-01-02 01:00:10+01:00"),
        ),
        (
            "2025-03-30T01:00:20+00:00",
            ("2025-03-30T00:59:50+00:00", "2025-03-30T01:00:00+00:00"),
            ("2025-03-30 01:59:50+01:00", "2025-03-30 03:00:00+02:00"),
        ),
    ],
)
def test_origin_and_ingestion_clocks_survive_midnight_and_dst(
    observed_at: str,
    source_times: tuple[str, str],
    expected_local_times: tuple[str, str],
    frozen_time,
) -> None:
    original_timezone = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(ZoneInfo("Europe/Madrid"))
    try:
        frozen_time.move_to(observed_at)
        coordinator = _coordinator_with_client(
            {
                "client_name": "test-client",
                "client_ip": "192.0.2.10",
                "query_count": 0,
                "allowed_count": 0,
                "blocked_count": 0,
                "last_query_id": 0,
                "recent_queries": [],
                "recent_activity": [],
            }
        )
        expected_observed = datetime.fromisoformat(observed_at).astimezone(UTC).timestamp()
        for query_id, source_time in enumerate(source_times, start=1):
            coordinator._update_client(
                {
                    "id": query_id,
                    "time": datetime.fromisoformat(source_time).timestamp(),
                    "domain": f"q{query_id}.example.test",
                    "client": {"ip": "192.0.2.10", "name": "test-client"},
                    "status": "FORWARDED",
                },
                query_id,
                count_as_new=True,
            )
        coordinator._update_minute_query_counts(expected_observed)
        summary = coordinator.client_data["192.0.2.10"]

        assert tuple(item["time"] for item in summary["recent_queries"]) == expected_local_times
        assert [item["observed_at"] for item in summary["recent_activity"]] == [
            expected_observed,
            expected_observed,
        ]
        assert summary["queries_observed_last_minute"] == 2
        assert summary["classifier_counts"]["1"]["allowed"] == 2
    finally:
        dt_util.set_default_time_zone(original_timezone)


@pytest.mark.asyncio
async def test_store_reload_preserves_legacy_offset_and_classified_epoch(frozen_time) -> None:
    saved = {
        "cursor": 200,
        "classifier_version": 1,
        "clients": {
            "192.0.2.10": {
                "query_count": 220,
                "allowed_count": 200,
                "blocked_count": 20,
                "classifier_version": 1,
                "classifier_counts": {"1": {"allowed": 8, "blocked": 3, "provisional": 2}},
                "recent_queries": [],
                "recent_activity": [],
            }
        },
    }

    class _Store:
        async def async_load(self):
            return saved

    coordinator = object.__new__(PiHoleQueryCoordinator)
    coordinator.store = _Store()
    coordinator.cursor = None
    coordinator._bootstrapped = False
    coordinator.client_data = {}
    await coordinator.async_load_cursor()

    summary = coordinator.client_data["192.0.2.10"]
    assert coordinator.cursor == 200
    assert summary["query_count"] == 220
    assert summary["allowed_count"] == 200
    assert summary["blocked_count"] == 20
    assert summary["classifier_counts"]["1"] == {
        "allowed": 8,
        "blocked": 3,
        "provisional": 2,
    }


def test_ha_sensor_exposes_ingestion_window_and_epoch_statistics() -> None:
    class _Coordinator:
        client_data = {
            "192.0.2.10": {
                "client_name": "test-client",
                "client_ip": "192.0.2.10",
                "query_count": 8,
                "queries_observed_last_minute": 4,
                "classifier_counts": {"1": {"allowed": 2, "blocked": 1, "provisional": 1}},
                "classifier_rates": {"1": {"allowed": 1, "blocked": 1, "provisional": 2}},
            }
        }

        def async_add_listener(self, _listener):
            return lambda: None

    entry = SimpleNamespace(unique_id="entry", entry_id="entry")
    descriptions = {description.key: description for description in SENSOR_DESCRIPTIONS}
    total = PiHoleClientSensor(_Coordinator(), entry, "192.0.2.10", descriptions["queries"])
    provisional = PiHoleClientSensor(
        _Coordinator(), entry, "192.0.2.10", descriptions["provisional_v1"]
    )
    attrs = total.extra_state_attributes
    assert attrs["queries_observed_last_minute"] == 4
    assert attrs["classifier_rates_last_minute"] == {"allowed": 1, "blocked": 1, "provisional": 2}
    assert provisional.native_value == 1
    assert attrs["blocked_percentage"] == pytest.approx(100 / 3)
    assert attrs["classified_count"] == 3


def test_last_minute_sensor_preserves_zero_and_missing_values() -> None:
    class _Coordinator:
        client_data = {"192.0.2.10": {"queries_last_minute": 4}}

        def async_add_listener(self, _listener):
            return lambda: None

    coordinator = _Coordinator()
    entry = SimpleNamespace(unique_id="entry", entry_id="entry")
    description = next(
        description
        for description in SENSOR_DESCRIPTIONS
        if description.key == "queries_last_minute"
    )
    sensor = PiHoleClientSensor(coordinator, entry, "192.0.2.10", description)

    assert sensor.native_value == 4
    assert sensor.entity_description.state_class is SensorStateClass.MEASUREMENT
    assert sensor.entity_description.native_unit_of_measurement == "consultas/min"
    assert sensor.extra_state_attributes["window_seconds"] == 60
    assert sensor.extra_state_attributes["timestamp_source"] == (
        "Pi-hole query.time, with HA observation fallback"
    )

    coordinator.client_data["192.0.2.10"]["queries_last_minute"] = 0
    assert sensor.native_value == 0
    del coordinator.client_data["192.0.2.10"]["queries_last_minute"]
    assert sensor.native_value is None


def test_last_minute_sensor_stays_unavailable_until_fresh_complete_window() -> None:
    class _Coordinator:
        client_data = {"192.0.2.10": {"queries_last_minute": 0}}
        last_update_success = True
        update_interval = timedelta(seconds=60)
        last_successful_read = dt_util.utcnow()
        coverage = "complete"
        query_time_window_ready = False

        def async_add_listener(self, _listener):
            return lambda: None

    coordinator = _Coordinator()
    entry = SimpleNamespace(unique_id="entry", entry_id="entry")
    description = next(
        description
        for description in SENSOR_DESCRIPTIONS
        if description.key == "queries_last_minute"
    )
    sensor = PiHoleClientSensor(coordinator, entry, "192.0.2.10", description)

    assert sensor.native_value == 0
    assert sensor.available is False
    coordinator.query_time_window_ready = True
    assert sensor.available is True
    coordinator.coverage = "partial"
    assert sensor.available is False
    coordinator.coverage = "complete"
    coordinator.last_update_success = False
    assert sensor.available is False


def test_source_time_window_needs_full_observation_span_and_future_time_resets_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sampled_at = 1_735_689_600.0
    coordinator = _coordinator_with_client(
        {
            "client_name": "test-client",
            "client_ip": "192.0.2.10",
            "recent_activity": [],
        }
    )
    coordinator.coverage = "complete"
    coordinator.awaiting_generation = False
    coordinator.retention_mode = "detail"
    coordinator._query_time_window_started_at = None
    coordinator.query_time_window_ready = False

    coordinator._update_minute_query_counts(sampled_at)
    assert coordinator.query_time_window_ready is False
    coordinator._update_minute_query_counts(sampled_at + 59.999)
    assert coordinator.query_time_window_ready is False
    coordinator._update_minute_query_counts(sampled_at + 60)
    assert coordinator.query_time_window_ready is False
    coordinator._update_minute_query_counts(sampled_at + 60.001)
    assert coordinator.query_time_window_ready is True

    monkeypatch.setattr(
        "custom_components.pihole_queries.coordinator.time.time",
        lambda: sampled_at + 60,
    )
    coordinator._update_client(
        {
            "time": sampled_at + 61,
            "status": "FORWARDED",
            "client": {"ip": "192.0.2.10", "name": "test-client"},
        },
        1,
        count_as_new=True,
    )
    assert coordinator.query_time_window_ready is False
    assert coordinator._query_time_window_started_at is None
    assert "query_time" not in coordinator.client_data["192.0.2.10"]["recent_activity"][-1]


def test_source_time_window_is_inclusive_and_separate_from_ha_observation_window() -> None:
    now = 1_735_689_600.75
    coordinator = _coordinator_with_client(
        {
            "classifier_version": 1,
            "recent_activity": [
                {
                    "observed_at": now,
                    "query_time": now - 60,
                    "classifier_version": 1,
                    "classification": "allowed",
                },
                {
                    "observed_at": now,
                    "query_time": now - 60.001,
                    "classifier_version": 1,
                    "classification": "blocked",
                },
                {
                    "observed_at": now,
                    "query_time": now,
                    "classifier_version": 1,
                    "classification": "provisional",
                },
                {
                    "observed_at": now,
                    "query_time": now + 0.001,
                    "classifier_version": 1,
                    "classification": "allowed",
                },
            ],
        }
    )
    coordinator.retention_mode = "detail"
    coordinator.coverage = "complete"
    coordinator.awaiting_generation = False
    coordinator._query_time_window_started_at = now - 61
    coordinator.query_time_window_ready = True

    coordinator._update_minute_query_counts(now)
    summary = coordinator.client_data["192.0.2.10"]

    assert summary["queries_last_minute"] == 2
    assert summary["queries_observed_last_minute"] == 4


def test_aggregate_source_time_bucket_boundary_is_at_most_one_second_coarse() -> None:
    now = 1_735_689_600.75
    coordinator = _coordinator_with_client(
        {
            "classifier_version": 1,
            "activity_buckets": [],
            "query_time_buckets": [
                {"second": int(now - 60), "count": 2},
                {"second": int(now - 60) - 1, "count": 3},
                {"second": int(now), "count": 1},
            ],
        }
    )
    coordinator.retention_mode = "aggregate"

    coordinator._update_aggregate_minute_counts(now)

    summary = coordinator.client_data["192.0.2.10"]
    # The partial boundary second is retained because aggregate mode does not
    # keep per-query timestamps; the preceding full second is discarded.
    assert summary["queries_last_minute"] == 3
