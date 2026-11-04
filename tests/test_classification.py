"""Regression coverage for literal, immutable query classifications."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityPlatformState

from custom_components.pihole_queries.classification import classify_status
from custom_components.pihole_queries.coordinator import (
    PiHoleQueryCoordinator,
    _query_description,
)
from custom_components.pihole_queries.sensor import (
    SENSOR_DESCRIPTIONS,
    PiHoleClientSensor,
)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("UNKNOWN", "provisional"),
        ("IN_PROGRESS", "provisional"),
        ("FORWARDED", "allowed"),
        ("CACHE", "allowed"),
        ("RETRIED", "allowed"),
        ("RETRIED_DNSSEC", "allowed"),
        ("CACHE_STALE", "allowed"),
        ("GRAVITY", "blocked"),
        ("REGEX", "blocked"),
        ("DENYLIST", "blocked"),
        ("EXTERNAL_BLOCKED_IP", "blocked"),
        ("EXTERNAL_BLOCKED_NULL", "blocked"),
        ("EXTERNAL_BLOCKED_NXRA", "blocked"),
        ("GRAVITY_CNAME", "blocked"),
        ("REGEX_CNAME", "blocked"),
        ("DENYLIST_CNAME", "blocked"),
        ("DBBUSY", "blocked"),
        ("SPECIAL_DOMAIN", "blocked"),
        ("EXTERNAL_BLOCKED_EDE15", "blocked"),
        ("INVALID", "provisional"),
        (None, "provisional"),
        ("NEW_FTL_STATE", "provisional"),
        ("GRAVITY_CNAME_SUFFIX", "provisional"),
        (42, "provisional"),
    ],
)
def test_contract_status_table_is_exact(status: Any, expected: str) -> None:
    assert classify_status(status) == expected


def test_absent_status_is_provisional_and_retains_raw_value() -> None:
    name, message = _query_description({"domain": "missing.example.test"})

    assert name.startswith("Pi-hole ·")
    assert "Provisional query" in message
    assert "Status: missing" in message


@pytest.mark.asyncio
async def test_new_observations_partition_into_exactly_one_class(frozen_time) -> None:
    coordinator = _coordinator(
        [
            _row(100, "FORWARDED"),
            _row(101, "FORWARDED"),
            _row(102, "GRAVITY"),
            _row(103, None),
        ]
    )
    coordinator.client_data = {}

    # IDs are newer than the cursor and all count once.
    result = await coordinator._async_update_data()
    summary = result["192.0.2.10"]
    counts = summary["class_counts"]
    assert summary["query_count"] == 3
    assert counts == {"allowed": 1, "blocked": 1, "provisional": 1}
    assert sum(counts.values()) == summary["query_count"]


@dataclass
class _Store:
    value: Any = None

    async def async_load(self) -> Any:
        return self.value

    async def async_save(self, value: Any) -> None:
        self.value = value


class _Services:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def async_call(self, _domain: str, _service: str, data: dict[str, Any], **_kwargs: Any) -> None:
        self.calls.append(data)


class _Api:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def async_get_queries(self, *, length: int, cursor: int | None = None) -> dict[str, Any]:
        rows = sorted(self.rows, key=lambda row: row["id"], reverse=True)
        if cursor is not None:
            rows = [row for row in rows if row["id"] <= cursor]
        return {"queries": rows[:length]}


def _coordinator(rows: list[dict[str, Any]], *, stored: dict[str, Any] | None = None):
    coordinator = object.__new__(PiHoleQueryCoordinator)
    coordinator.client = _Api(rows)
    coordinator.store = _Store(stored)
    coordinator.hass = SimpleNamespace(services=_Services())
    coordinator.cursor = (stored or {}).get("cursor", 100)
    coordinator._bootstrapped = True
    coordinator.client_data = (stored or {}).get(
        "clients",
        {
            "192.0.2.10": {
                "client_name": "test-client",
                "client_ip": "192.0.2.10",
                "query_count": 5,
                "allowed_count": 4,
                "blocked_count": 1,
            }
        },
    )
    return coordinator


def _row(query_id: int, status: Any) -> dict[str, Any]:
    return {
        "id": query_id,
        "time": 1_735_689_600,
        "domain": f"q{query_id}.example.test",
        "client": {"ip": "192.0.2.10", "name": "test-client"},
        "status": status,
    }


@pytest.mark.asyncio
async def test_unknown_first_observation_is_not_reclassified_or_recaptured(frozen_time) -> None:
    row = _row(101, "UNKNOWN")
    coordinator = _coordinator([_row(100, "FORWARDED"), row])

    first = await coordinator._async_update_data()
    summary = first["192.0.2.10"]
    assert summary["query_count"] == 6
    assert summary["allowed_count"] == 4
    assert summary["blocked_count"] == 1
    assert summary["class_counts"] == {
        "allowed": 0,
        "blocked": 0,
        "provisional": 1,
    }
    assert "last_query" not in summary
    assert "recent_queries" not in summary
    assert len(coordinator.hass.services.calls) == 1

    coordinator.client.rows = [_row(101, "GRAVITY")]
    second = await coordinator._async_update_data()

    assert second["192.0.2.10"]["query_count"] == 6
    assert second["192.0.2.10"]["class_counts"]["provisional"] == 1
    assert "recent_queries" not in second["192.0.2.10"]
    assert len(coordinator.hass.services.calls) == 1


@pytest.mark.asyncio
async def test_overlapping_api_pages_count_and_log_each_id_once(frozen_time) -> None:
    class _OverlappingApi:
        async def async_get_queries(self, *, length: int, cursor: int | None = None) -> dict[str, Any]:
            if cursor is None:
                return {"queries": [_row(102, "GRAVITY"), _row(101, "UNKNOWN")]}
            assert cursor == 100
            return {"queries": [_row(101, "GRAVITY"), _row(100, "FORWARDED")]}

    coordinator = _coordinator([])
    coordinator.client = _OverlappingApi()

    data = await coordinator._async_update_data()
    summary = data["192.0.2.10"]

    assert summary["query_count"] == 7
    assert summary["class_counts"] == {
        "allowed": 0,
        "blocked": 1,
        "provisional": 1,
    }
    assert "recent_queries" not in summary
    assert len(summary["activity_buckets"]) == 1
    assert summary["queries_observed_last_minute"] == 2
    assert len(coordinator.hass.services.calls) == 2


@pytest.mark.asyncio
async def test_missing_and_null_status_are_provisional_in_ingestion_and_store(frozen_time) -> None:
    absent = _row(101, "UNUSED")
    absent.pop("status")
    explicit_null = _row(102, None)
    coordinator = _coordinator([_row(100, "FORWARDED"), absent, explicit_null])

    data = await coordinator._async_update_data()
    summary = data["192.0.2.10"]
    assert summary["query_count"] == 7
    assert summary["class_counts"] == {
        "allowed": 0,
        "blocked": 0,
        "provisional": 2,
    }
    assert "recent_queries" not in summary
    assert "Status: missing" in coordinator.hass.services.calls[0]["message"]
    assert "Status: None" in coordinator.hass.services.calls[1]["message"]
    assert coordinator.store.value["clients"]["192.0.2.10"]["class_counts"]["provisional"] == 2

    total = PiHoleClientSensor(
        coordinator,
        SimpleNamespace(unique_id="entry", entry_id="entry", data={"source_id": "source", "source_generation": "first"}),
        "192.0.2.10",
        next(description for description in SENSOR_DESCRIPTIONS if description.key == "queries"),
    )
    assert total.extra_state_attributes["blocked_percentage"] is None


@pytest.mark.asyncio
async def test_ha_state_machine_keeps_total_and_class_counters_monotonic(tmp_path) -> None:
    class _Coordinator:
        client_data = {
            "192.0.2.10": {
                "client_name": "test-client",
                "client_ip": "192.0.2.10",
                "query_count": 50,
                "allowed_count": 12,
                "blocked_count": 4,
                "class_counts": {"allowed": 0, "blocked": 0, "provisional": 0},
            }
        }
        last_update_success = True

        def async_add_listener(self, _listener):
            return lambda: None

    entry = SimpleNamespace(unique_id="entry", entry_id="entry", data={"source_id": "source", "source_generation": "first"})
    coordinator = _Coordinator()
    descriptions = {description.key: description for description in SENSOR_DESCRIPTIONS}
    total = PiHoleClientSensor(coordinator, entry, "192.0.2.10", descriptions["queries"])
    allowed = PiHoleClientSensor(coordinator, entry, "192.0.2.10", descriptions["allowed"])

    hass = HomeAssistant(str(tmp_path))
    await hass.async_start()
    entities = (total, allowed)
    for entity in entities:
        entity.hass = hass
        entity.entity_id = f"sensor.pihole_{entity.entity_description.key}"
        entity.platform = SimpleNamespace(
            platform_name="sensor",
            domain="sensor",
            config_entry=entry,
            component_translations={},
            platform_translations={},
                default_language_platform_translations={},
        )
        entity.platform_data = entity.platform
        await entity.async_internal_added_to_hass()
        entity._platform_state = EntityPlatformState.ADDED
        entity.async_write_ha_state()

    assert total.unique_id == "source_first_192.0.2.10"
    assert allowed.unique_id == "source_first_192.0.2.10_allowed"
    assert hass.states.get(total.entity_id).attributes["state_class"] == "total_increasing"
    assert hass.states.get(allowed.entity_id).attributes["state_class"] == "total_increasing"
    assert hass.states.get(total.entity_id).state == "50"
    assert hass.states.get(allowed.entity_id).state == "0"

    data = coordinator.client_data["192.0.2.10"]
    data["query_count"] = 51
    data["class_counts"]["allowed"] = 1
    for entity in (total, allowed):
        entity.async_write_ha_state()
    assert hass.states.get(total.entity_id).state == "51"
    assert hass.states.get(allowed.entity_id).state == "1"
    await hass.async_stop()


def test_sensor_cardinality_stays_within_all_supported_profiles() -> None:
    from custom_components.pihole_queries.sensor import DIAGNOSTIC_DESCRIPTIONS

    profiles = ((5, 30), (25, 150), (100, 600))
    assert len(SENSOR_DESCRIPTIONS) == 5
    assert {description.key for description in SENSOR_DESCRIPTIONS} == {
        "queries",
        "queries_last_minute",
        "allowed",
        "blocked",
        "provisional",
    }
    assert len(DIAGNOSTIC_DESCRIPTIONS) == 4
    expected_totals = (29, 129, 504)
    for (clients, entity_budget), expected_total in zip(profiles, expected_totals, strict=True):
        created_entities = clients * len(SENSOR_DESCRIPTIONS) + len(DIAGNOSTIC_DESCRIPTIONS)
        assert created_entities == expected_total
        assert clients * len(SENSOR_DESCRIPTIONS) + len(DIAGNOSTIC_DESCRIPTIONS) <= entity_budget
