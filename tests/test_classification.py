"""Regression coverage for literal, immutable query classifications."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.components.sensor import SensorEntityDescription, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityPlatformState

from custom_components.pihole_queries import classification, coordinator as coordinator_module
from custom_components.pihole_queries.classification import classify_status
from custom_components.pihole_queries.coordinator import (
    PiHoleQueryCoordinator,
    _query_description,
)
from custom_components.pihole_queries.sensor import (
    PiHoleClientSensor,
    SENSOR_DESCRIPTIONS,
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
    assert "Consulta provisional" in message
    assert "Estado: ausente" in message


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

    # IDs are newer than the cursor and all belong to the same first epoch.
    result = await coordinator._async_update_data()
    summary = result["192.0.2.10"]
    counts = summary["classifier_counts"]["1"]
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
                "last_query_id": 100,
                "recent_queries": [],
                "recent_activity": [],
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
    assert summary["classifier_counts"]["1"] == {
        "allowed": 0,
        "blocked": 0,
        "provisional": 1,
    }
    assert summary["recent_queries"][-1]["status"] == "UNKNOWN"
    assert summary["recent_queries"][-1]["result"] == "provisional"
    assert summary["recent_queries"][-1]["classification"] == "provisional"
    assert len(coordinator.hass.services.calls) == 1

    coordinator.client.rows = [_row(101, "GRAVITY")]
    second = await coordinator._async_update_data()

    assert second["192.0.2.10"]["query_count"] == 6
    assert second["192.0.2.10"]["classifier_counts"]["1"]["provisional"] == 1
    assert second["192.0.2.10"]["recent_queries"][-1]["status"] == "UNKNOWN"
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
    assert summary["classifier_counts"]["1"] == {
        "allowed": 0,
        "blocked": 1,
        "provisional": 1,
    }
    assert [item["id"] for item in summary["recent_queries"]] == [101, 102]
    assert summary["recent_queries"][0]["status"] == "UNKNOWN"
    assert len(coordinator.client_data["192.0.2.10"]["recent_activity"]) == 2
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
    recent = {item["id"]: item for item in summary["recent_queries"]}

    assert summary["query_count"] == 7
    assert summary["classifier_counts"]["1"] == {
        "allowed": 0,
        "blocked": 0,
        "provisional": 2,
    }
    assert recent[101]["status"] is None
    assert recent[101]["status_present"] is False
    assert recent[101]["classification"] == "provisional"
    assert recent[102]["status"] is None
    assert recent[102]["status_present"] is True
    assert "Estado: ausente" in coordinator.hass.services.calls[0]["message"]
    assert "Estado: None" in coordinator.hass.services.calls[1]["message"]
    assert coordinator.store.value["clients"]["192.0.2.10"]["classifier_counts"]["1"]["provisional"] == 2

    total = PiHoleClientSensor(
        coordinator,
        SimpleNamespace(unique_id="entry", entry_id="entry"),
        "192.0.2.10",
        next(description for description in SENSOR_DESCRIPTIONS if description.key == "queries"),
    )
    assert total.extra_state_attributes["blocked_percentage"] is None


@pytest.mark.asyncio
async def test_classifier_change_starts_a_new_epoch_without_reclassifying_old_ids(
    monkeypatch, frozen_time
) -> None:
    old = {
        "cursor": 100,
        "clients": {
            "192.0.2.10": {
                "client_name": "test-client",
                "client_ip": "192.0.2.10",
                "query_count": 10,
                "allowed_count": 8,
                "blocked_count": 2,
                "classifier_version": 1,
                "classifier_counts": {"1": {"allowed": 3, "blocked": 2, "provisional": 1}},
                "last_query_id": 100,
                "recent_queries": [],
                "recent_activity": [],
            }
        },
    }
    monkeypatch.setattr(coordinator_module, "CLASSIFIER_VERSION", 2)
    monkeypatch.setattr(classification, "CLASSIFIER_VERSION", 2)
    monkeypatch.setitem(classification.STATUS_CLASSIFIERS, 2, {"FORWARDED": "blocked"})
    coordinator = _coordinator([_row(100, "FORWARDED"), _row(101, "FORWARDED")], stored=old)

    data = await coordinator._async_update_data()
    summary = data["192.0.2.10"]

    assert summary["query_count"] == 11
    assert summary["classifier_counts"]["1"] == {
        "allowed": 3,
        "blocked": 2,
        "provisional": 1,
    }
    assert summary["classifier_counts"]["2"] == {
        "allowed": 0,
        "blocked": 1,
        "provisional": 0,
    }
    assert summary["allowed_count"] == 8
    assert summary["blocked_count"] == 2


@pytest.mark.asyncio
async def test_loading_legacy_epoch_activates_empty_current_epoch(monkeypatch) -> None:
    monkeypatch.setattr(coordinator_module, "CLASSIFIER_VERSION", 2)
    monkeypatch.setattr(classification, "CLASSIFIER_VERSION", 2)
    store = _Store(
        {
            "cursor": 100,
            "clients": {
                "192.0.2.10": {
                    "query_count": 20,
                    "allowed_count": 18,
                    "blocked_count": 2,
                    "classifier_version": 1,
                    "classifier_counts": {"1": {"allowed": 7, "blocked": 2, "provisional": 1}},
                }
            },
        }
    )
    coordinator = object.__new__(PiHoleQueryCoordinator)
    coordinator.store = store
    coordinator.cursor = None
    coordinator._bootstrapped = False
    coordinator.client_data = {}

    await coordinator.async_load_cursor()
    summary = coordinator.client_data["192.0.2.10"]

    assert summary["classifier_version"] == 2
    assert summary["classifier_counts"]["1"] == {
        "allowed": 7,
        "blocked": 2,
        "provisional": 1,
    }
    assert summary["classifier_counts"]["2"] == {
        "allowed": 0,
        "blocked": 0,
        "provisional": 0,
    }
    assert summary["query_count"] == 20
    assert summary["allowed_count"] == 18
    assert summary["blocked_count"] == 2


@pytest.mark.asyncio
async def test_ha_state_machine_keeps_total_and_class_epochs_monotonic(tmp_path) -> None:
    class _Coordinator:
        client_data = {
            "192.0.2.10": {
                "client_name": "test-client",
                "client_ip": "192.0.2.10",
                "query_count": 50,
                "allowed_count": 12,
                "blocked_count": 4,
                "classifier_version": 1,
                "classifier_counts": {"1": {"allowed": 0, "blocked": 0, "provisional": 0}},
            }
        }
        last_update_success = True

        def async_add_listener(self, _listener):
            return lambda: None

    entry = SimpleNamespace(unique_id="entry", entry_id="entry")
    coordinator = _Coordinator()
    descriptions = {description.key: description for description in SENSOR_DESCRIPTIONS}
    total = PiHoleClientSensor(coordinator, entry, "192.0.2.10", descriptions["queries"])
    epoch_one = PiHoleClientSensor(
        coordinator, entry, "192.0.2.10", descriptions["allowed_v1"]
    )
    epoch_two = PiHoleClientSensor(
        coordinator,
        entry,
        "192.0.2.10",
        SensorEntityDescription(
            key="allowed_v2",
            name="Permitidas (v2)",
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
    )

    hass = HomeAssistant(str(tmp_path))
    await hass.async_start()
    entities = (total, epoch_one, epoch_two)
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

    assert total.unique_id == "entry_192.0.2.10"
    assert epoch_one.unique_id == "entry_192.0.2.10_allowed_v1"
    assert epoch_two.unique_id == "entry_192.0.2.10_allowed_v2"
    assert hass.states.get(total.entity_id).attributes["state_class"] == "total_increasing"
    assert hass.states.get(epoch_one.entity_id).attributes["state_class"] == "total_increasing"
    assert hass.states.get(epoch_two.entity_id).attributes["state_class"] == "total_increasing"
    assert hass.states.get(total.entity_id).state == "50"
    assert hass.states.get(epoch_one.entity_id).state == "0"

    data = coordinator.client_data["192.0.2.10"]
    data["classifier_version"] = 2
    data["query_count"] = 51
    data["classifier_counts"]["1"]["allowed"] = 1
    data["classifier_counts"]["2"] = {"allowed": 0, "blocked": 0, "provisional": 0}
    for entity in (total, epoch_one, epoch_two):
        entity.async_write_ha_state()
    assert hass.states.get(total.entity_id).state == "51"
    assert hass.states.get(epoch_one.entity_id).state == "1"
    assert hass.states.get(epoch_two.entity_id).state == "0"

    data["query_count"] = 52
    data["classifier_counts"]["2"]["allowed"] = 1
    for entity in (total, epoch_one, epoch_two):
        entity.async_write_ha_state()
    assert hass.states.get(total.entity_id).state == "52"
    assert hass.states.get(epoch_one.entity_id).state == "1"
    assert hass.states.get(epoch_two.entity_id).state == "1"
    await hass.async_stop()


def test_sensor_cardinality_stays_within_all_supported_profiles() -> None:
    from custom_components.pihole_queries.sensor import DIAGNOSTIC_DESCRIPTIONS

    profiles = ((5, 30), (25, 150), (100, 600))
    assert len(SENSOR_DESCRIPTIONS) == 4
    assert {description.key for description in SENSOR_DESCRIPTIONS} == {
        "queries",
        "allowed_v1",
        "blocked_v1",
        "provisional_v1",
    }
    assert len(DIAGNOSTIC_DESCRIPTIONS) == 4
    expected_totals = (24, 104, 404)
    for (clients, entity_budget), expected_total in zip(profiles, expected_totals, strict=True):
        created_entities = clients * len(SENSOR_DESCRIPTIONS) + len(DIAGNOSTIC_DESCRIPTIONS)
        assert created_entities == expected_total
        assert clients * len(SENSOR_DESCRIPTIONS) + len(DIAGNOSTIC_DESCRIPTIONS) <= entity_budget
