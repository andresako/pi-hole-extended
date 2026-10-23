"""Diagnostics must expose only evidence the integration can verify."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import EntityCategory

from custom_components.pihole_queries.const import DOMAIN
from custom_components.pihole_queries.coordinator import PiHoleQueryCoordinator
from custom_components.pihole_queries import sensor as sensor_module
from custom_components.pihole_queries.sensor import (
    DIAGNOSTIC_DESCRIPTIONS,
    PiHoleDiagnosticSensor,
    async_setup_entry,
)


def _coordinator(
    *,
    now: datetime,
    interval: int = 15,
    last_update_success: bool | None = True,
    last_successful_read: datetime | None = None,
    last_query_time: float | None = None,
    coverage: str = "unknown",
    coverage_reason: str | None = "no_baseline",
) -> SimpleNamespace:
    return SimpleNamespace(
        async_add_listener=lambda _listener: lambda: None,
        update_interval=timedelta(seconds=interval),
        last_update_success=last_update_success,
        last_successful_read=last_successful_read,
        last_api_attempt_successful=(True if last_successful_read is not None else None),
        last_query_time=last_query_time,
        coverage=coverage,
        coverage_reason=coverage_reason,
        client_data={},
    )


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="synthetic-entry", title="Pi-hole test", async_on_unload=lambda _unload: None)


def _sensor(coordinator: SimpleNamespace, key: str) -> PiHoleDiagnosticSensor:
    description = next(item for item in DIAGNOSTIC_DESCRIPTIONS if item.key == key)
    return PiHoleDiagnosticSensor(coordinator, _entry(), description)


@pytest.mark.parametrize("interval", [15, 120, 3600])
def test_communication_freshness_uses_twice_configured_interval(monkeypatch, interval: int) -> None:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    clock = [now]
    monkeypatch.setattr(sensor_module.dt_util, "utcnow", lambda: clock[0])
    coordinator = _coordinator(
        now=now,
        interval=interval,
        last_successful_read=now - timedelta(seconds=interval * 2),
    )
    sensor = _sensor(coordinator, "communication_status")

    assert sensor.native_value == "connected"
    assert sensor.extra_state_attributes["freshness_threshold_seconds"] == interval * 2
    clock[0] = now + timedelta(seconds=0.001)
    assert sensor.native_value == "stale"


def test_communication_states_cover_bootstrap_failure_and_recovery(monkeypatch) -> None:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(sensor_module.dt_util, "utcnow", lambda: now)
    coordinator = _coordinator(now=now, last_update_success=None)
    sensor = _sensor(coordinator, "communication_status")
    assert sensor.native_value == "unknown"

    coordinator.last_api_attempt_successful = False  # Includes rejected credentials/API auth failure.
    assert sensor.native_value == "error"
    coordinator.last_successful_read = now
    coordinator.last_api_attempt_successful = True
    coordinator.last_update_success = False  # A later Activity or Store failure is not an API error.
    assert sensor.native_value == "connected"


@pytest.mark.parametrize(
    ("coverage", "reason"),
    [("unknown", "empty_history"), ("complete", None), ("partial", "retention_truncation")],
)
def test_coverage_reports_api_evidence_without_claiming_persistence(
    coverage: str, reason: str | None
) -> None:
    coordinator = _coordinator(
        now=datetime(2025, 1, 1, tzinfo=UTC),
        coverage=coverage,
        coverage_reason=reason,
    )
    sensor = _sensor(coordinator, "coverage")

    assert sensor.native_value == coverage
    assert sensor.extra_state_attributes["coverage_reason"] == reason
    assert sensor.extra_state_attributes["activity_persistence"] == "unverified"
    assert sensor.extra_state_attributes["recorder_retention"] == "unknown"


@pytest.mark.parametrize("sample_age", [0, 60, 3600])
def test_last_query_sensor_reports_source_time_and_age(monkeypatch, sample_age: int) -> None:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(sensor_module.dt_util, "utcnow", lambda: now)
    sample = now - timedelta(seconds=sample_age)
    coordinator = _coordinator(now=now, last_query_time=sample.timestamp())
    sensor = _sensor(coordinator, "last_query_observed")

    assert sensor.native_value == sample
    assert sensor.extra_state_attributes["timestamp_source"] == "Pi-hole query.time"
    assert sensor.extra_state_attributes["sample_age_seconds"] == sample_age
    assert sensor.extra_state_attributes["sample_age_status"] == "known"


@pytest.mark.parametrize("timestamp", [None, float("inf"), float("nan"), True])
def test_missing_or_invalid_sample_is_unknown_not_zero(monkeypatch, timestamp) -> None:
    monkeypatch.setattr(sensor_module.dt_util, "utcnow", lambda: datetime(2025, 1, 1, tzinfo=UTC))
    coordinator = _coordinator(now=datetime(2025, 1, 1, tzinfo=UTC), last_query_time=timestamp)
    sensor = _sensor(coordinator, "last_query_observed")

    assert sensor.native_value is None
    assert sensor.extra_state_attributes["sample_age_seconds"] is None
    assert sensor.extra_state_attributes["sample_age_status"] == "unknown"


def test_future_source_timestamp_has_unknown_age(monkeypatch) -> None:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(sensor_module.dt_util, "utcnow", lambda: now)
    coordinator = _coordinator(now=now, last_query_time=(now + timedelta(seconds=120)).timestamp())
    sensor = _sensor(coordinator, "last_query_observed")

    assert sensor.native_value is None
    assert sensor.extra_state_attributes["sample_age_seconds"] is None


@pytest.mark.asyncio
async def test_setup_adds_a_fixed_small_diagnostic_set() -> None:
    coordinator = _coordinator(now=datetime(2025, 1, 1, tzinfo=UTC))
    hass = SimpleNamespace(data={DOMAIN: {"synthetic-entry": {"coordinator": coordinator}}})
    added = []

    await async_setup_entry(hass, _entry(), added.extend)

    diagnostics = [entity for entity in added if entity.entity_description.entity_category == EntityCategory.DIAGNOSTIC]
    assert {entity.entity_description.key for entity in diagnostics} == {
        "communication_status",
        "last_successful_read",
        "last_query_observed",
        "coverage",
    }
    assert all(entity.available for entity in diagnostics)
    assert len({entity.unique_id for entity in diagnostics}) == 4


def test_diagnostic_state_translations_cover_every_emitted_status() -> None:
    component = Path("custom_components/pihole_queries")
    expected_states = {
        "communication_status": {"connected", "error", "stale", "unknown"},
        "coverage": {"unknown", "complete", "partial"},
    }
    for locale in ("en", "es"):
        translation = json.loads((component / "translations" / f"{locale}.json").read_text())
        entities = translation["entity"]["sensor"]
        for key, states in expected_states.items():
            assert set(entities[key]["state"]) == states


@pytest.mark.asyncio
async def test_successful_read_timestamp_advances_only_after_success(monkeypatch) -> None:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    monkeypatch.setattr("custom_components.pihole_queries.coordinator.dt_util.utcnow", lambda: now)
    coordinator = object.__new__(PiHoleQueryCoordinator)
    coordinator.last_successful_read = None
    coordinator.client = SimpleNamespace(async_get_queries=AsyncMock(return_value={"queries": []}))

    result = await coordinator._async_get_queries(length=1000)

    assert result == {"queries": []}
    assert coordinator.last_successful_read == now
    assert coordinator.last_api_attempt_successful is True

    previous = coordinator.last_successful_read
    coordinator.client.async_get_queries = AsyncMock(side_effect=RuntimeError("synthetic auth failure"))
    with pytest.raises(RuntimeError, match="synthetic auth failure"):
        await coordinator._async_get_queries(length=1000)
    assert coordinator.last_successful_read == previous
    assert coordinator.last_api_attempt_successful is False
