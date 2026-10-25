"""Unusual-activity sensors: summary, monitor, entity state and options."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.pihole_queries import activity_monitor as monitor_module
from custom_components.pihole_queries.activity import params_for
from custom_components.pihole_queries.activity_monitor import (
    UnusualActivityMonitor,
    summarize,
)
from custom_components.pihole_queries.binary_sensor import PiHoleUnusualActivitySensor
from custom_components.pihole_queries.config_flow import _schema
from custom_components.pihole_queries.const import (
    CONF_SOURCE_GENERATION,
    CONF_UNUSUAL_ACTIVITY,
    LEGACY_SOURCE_GENERATION,
)
from custom_components.pihole_queries import _canonical_entity_id


def _series(unusual: bool, runs=None, baseline: bool = True):
    latest = {"t": 1, "count": 0, "rate": 120.0, "base": 4.0, "threshold": 40.0, "unusual": unusual}
    return {
        "has_baseline": baseline,
        "latest": latest if baseline else None,
        "runs": runs or [],
    }


def test_summary_without_baseline_is_unknown() -> None:
    assert summarize(_series(False, baseline=False)) == {"ready": False, "unusual": None}


def test_summary_reports_rate_multiple_and_start_of_run() -> None:
    result = summarize(_series(True, runs=[{"start": 1000.0, "end": 2000.0}]))
    assert result["unusual"] is True
    assert result["multiple"] == pytest.approx(30.0)
    assert result["since"] == 1000.0
    assert summarize(_series(False))["since"] is None


ENTRY = SimpleNamespace(
    entry_id="entry-1",
    unique_id="pi-hole.test",
    data={CONF_SOURCE_GENERATION: LEGACY_SOURCE_GENERATION},
    options={},
    async_on_unload=lambda unsub: None,
)


def _monitor(client_keys, entity_for_key):
    coordinator = SimpleNamespace(client_data={key: {} for key in client_keys})
    monitor = UnusualActivityMonitor(MagicMock(), ENTRY, coordinator)
    registry = MagicMock(
        async_get_entity_id=lambda domain, platform, unique_id: entity_for_key.get(unique_id)
    )
    return monitor, registry


async def test_monitor_stores_a_result_per_client_and_notifies() -> None:
    monitor, registry = _monitor(
        ["192.0.2.10", "192.0.2.11"],
        {
            "pi-hole.test_192.0.2.10": "sensor.phe_192_0_2_10_consultas_dns",
            "pi-hole.test_192.0.2.11": "sensor.phe_192_0_2_11_consultas_dns",
        },
    )
    calls = []
    monitor.async_add_listener(lambda: calls.append(1))
    data = {
        "series": {
            "sensor.phe_192_0_2_10_consultas_dns": _series(True, runs=[{"start": 5.0, "end": 9.0}]),
            "sensor.phe_192_0_2_11_consultas_dns": _series(False),
        }
    }
    with (
        patch.object(monitor_module.er, "async_get", return_value=registry),
        patch.object(monitor_module, "async_client_series", AsyncMock(return_value=data)) as read,
    ):
        await monitor._async_evaluate()

    assert monitor.results["192.0.2.10"]["unusual"] is True
    assert monitor.results["192.0.2.11"]["unusual"] is False
    assert calls == [1]
    # One Recorder read covering both counters, scored with the medium defaults.
    assert read.await_count == 1
    assert read.await_args.args[1] == {
        "sensor.phe_192_0_2_10_consultas_dns",
        "sensor.phe_192_0_2_11_consultas_dns",
    }
    assert read.await_args.args[4]["sensor.phe_192_0_2_10_consultas_dns"] == params_for("medium")


async def test_monitor_keeps_previous_results_when_the_recorder_fails() -> None:
    monitor, registry = _monitor(["192.0.2.10"], {"pi-hole.test_192.0.2.10": "sensor.x"})
    monitor.results = {"192.0.2.10": {"ready": True, "unusual": True}}
    with (
        patch.object(monitor_module.er, "async_get", return_value=registry),
        patch.object(monitor_module, "async_client_series", AsyncMock(side_effect=RuntimeError)),
    ):
        await monitor._async_evaluate()
    assert monitor.results["192.0.2.10"]["unusual"] is True


async def test_monitor_without_registered_counters_does_nothing() -> None:
    monitor, registry = _monitor(["192.0.2.10"], {})
    with (
        patch.object(monitor_module.er, "async_get", return_value=registry),
        patch.object(monitor_module, "async_client_series", AsyncMock()) as read,
    ):
        await monitor._async_evaluate()
    read.assert_not_awaited()


def _sensor(results):
    coordinator = SimpleNamespace(client_data={"192.0.2.10": {"client_name": "tv.home", "client_ip": "192.0.2.10"}})
    monitor = SimpleNamespace(results=results, evaluated_at=datetime(2026, 1, 1, tzinfo=UTC))
    return PiHoleUnusualActivitySensor(coordinator, monitor, ENTRY, "192.0.2.10")


def test_sensor_state_is_unknown_until_there_is_a_baseline() -> None:
    sensor = _sensor({"192.0.2.10": {"ready": False, "unusual": None}})
    assert sensor.is_on is None
    assert sensor.extra_state_attributes is None
    assert _sensor({}).is_on is None


def test_sensor_exposes_rates_and_start() -> None:
    summary = summarize(_series(True, runs=[{"start": 1_735_689_600.0, "end": 1_735_690_000.0}]))
    sensor = _sensor({"192.0.2.10": summary})
    assert sensor.is_on is True
    attrs = sensor.extra_state_attributes
    assert attrs["current_rate"] == 120.0
    assert attrs["usual_rate"] == 4.0
    assert attrs["times_usual"] == 30.0
    assert attrs["since"] == datetime(2025, 1, 1, tzinfo=UTC)
    assert sensor.unique_id == "pi-hole.test_192.0.2.10_unusual_activity"
    assert sensor.entity_description.translation_key == "unusual_activity"


def test_sensor_id_is_normalised_under_phe() -> None:
    entity = SimpleNamespace(domain="binary_sensor", unique_id="pi-hole.test_192.0.2.10_unusual_activity")
    assert (
        _canonical_entity_id(ENTRY, entity)
        == "binary_sensor.phe_192_0_2_10_actividad_inusual"
    )


def test_options_default_to_medium_and_validate_levels() -> None:
    schema = _schema(include_connection=False, include_retention=True)
    assert schema({"scan_interval": 15})[CONF_UNUSUAL_ACTIVITY] == "medium"
    for level in ("off", "low", "medium", "high"):
        assert schema({"scan_interval": 15, CONF_UNUSUAL_ACTIVITY: level})[CONF_UNUSUAL_ACTIVITY] == level
    with pytest.raises(Exception):
        schema({"scan_interval": 15, CONF_UNUSUAL_ACTIVITY: "extreme"})


def test_sensitivity_levels_are_ordered() -> None:
    low, medium, high = (params_for(level) for level in ("low", "medium", "high"))
    assert low.ratio > medium.ratio > high.ratio
    assert low.min_rate > medium.min_rate > high.min_rate
    assert low.min_run_seconds > medium.min_run_seconds > high.min_run_seconds
    assert params_for("off") == medium
    assert params_for(None) == medium


async def test_platform_creates_one_sensor_per_client_unless_off() -> None:
    from custom_components.pihole_queries import binary_sensor as platform
    from custom_components.pihole_queries.const import DOMAIN

    def run(options):
        coordinator = SimpleNamespace(
            client_data={"192.0.2.10": {}, "192.0.2.11": {}},
            async_add_listener=lambda listener: (lambda: None),
        )
        hass = SimpleNamespace(data={DOMAIN: {"entry-1": {"coordinator": coordinator}}})
        entry = SimpleNamespace(**{**ENTRY.__dict__, "options": options})
        added = []
        return hass, entry, added

    hass, entry, added = run({CONF_UNUSUAL_ACTIVITY: "off"})
    with patch.object(platform, "UnusualActivityMonitor") as monitor:
        await platform.async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    assert added == []
    monitor.assert_not_called()

    hass, entry, added = run({})
    with patch.object(platform, "UnusualActivityMonitor") as monitor:
        await platform.async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    assert [e.unique_id for e in added] == [
        "pi-hole.test_192.0.2.10_unusual_activity",
        "pi-hole.test_192.0.2.11_unusual_activity",
    ]
    monitor.return_value.async_start.assert_called_once_with()
