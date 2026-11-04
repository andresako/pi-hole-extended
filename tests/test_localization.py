"""Entity and device names stay localized without changing registry identity."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from custom_components.pihole_queries.const import DOMAIN
from custom_components.pihole_queries.entity_ids import (
    canonical_entity_id,
    diagnostic_entity_id,
)
from custom_components.pihole_queries.sensor import (
    DIAGNOSTIC_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
    PiHoleClientSensor,
    _client_entity_unique_id,
)

COMPONENT = Path(__file__).parents[1] / "custom_components" / DOMAIN


def _keys(value):
    if isinstance(value, dict):
        return {key: _keys(item) for key, item in value.items()}
    return None


def test_base_and_shipped_translations_have_the_same_shape() -> None:
    catalogs = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (
            COMPONENT / "strings.json",
            COMPONENT / "translations" / "en.json",
            COMPONENT / "translations" / "es.json",
        )
    ]

    assert _keys(catalogs[0]) == _keys(catalogs[1]) == _keys(catalogs[2])


def test_base_catalog_is_english() -> None:
    base = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    english = json.loads(
        (COMPONENT / "translations" / "en.json").read_text(encoding="utf-8")
    )

    assert base == english


def test_all_client_and_diagnostic_sensor_names_have_both_translations() -> None:
    translations = {
        locale: json.loads(
            (COMPONENT / "translations" / f"{locale}.json").read_text(encoding="utf-8")
        )
        for locale in ("en", "es")
    }
    descriptions = (*SENSOR_DESCRIPTIONS, *DIAGNOSTIC_DESCRIPTIONS)

    for description in descriptions:
        assert description.translation_key
        for catalog in translations.values():
            assert catalog["entity"]["sensor"][description.translation_key]["name"]

    assert translations["en"]["entity"]["sensor"]["allowed_v1"]["name"] == "Allowed queries"
    assert translations["en"]["entity"]["sensor"]["blocked_v1"]["name"] == "Blocked queries"
    assert translations["en"]["entity"]["sensor"]["provisional_v1"]["name"] == "Provisional queries"
    assert "v1" not in " ".join(
        translations["en"]["entity"]["sensor"][key]["name"]
        for key in ("allowed_v1", "blocked_v1", "provisional_v1")
    )


def test_translation_changes_do_not_change_client_unique_ids() -> None:
    entry = SimpleNamespace(unique_id="stable-entry", entry_id="entry-id")
    expected = {
        "queries": "stable-entry_192.0.2.10",
        "queries_last_minute": "stable-entry_192.0.2.10_queries_last_minute",
        "allowed_v1": "stable-entry_192.0.2.10_allowed_v1",
        "blocked_v1": "stable-entry_192.0.2.10_blocked_v1",
        "provisional_v1": "stable-entry_192.0.2.10_provisional_v1",
    }

    for description in SENSOR_DESCRIPTIONS:
        assert _client_entity_unique_id(entry, "192.0.2.10", description.key) == expected[
            description.key
        ]


def test_unknown_client_device_fallback_uses_home_assistant_language() -> None:
    entry = SimpleNamespace(unique_id="stable-entry", entry_id="entry-id")
    description = SENSOR_DESCRIPTIONS[0]
    actual_ids = set()

    for language, expected_name in (
        ("en-US", "Unknown client"),
        ("es-ES", "Cliente desconocido"),
        ("fr", "Unknown client"),
    ):
        coordinator = SimpleNamespace(
            client_data={"unknown": {"client_name": "Unknown client"}},
            hass=SimpleNamespace(config=SimpleNamespace(language=language)),
        )
        sensor = PiHoleClientSensor(coordinator, entry, "unknown", description)

        assert sensor.device_info["translation_key"] == "client_device"
        assert sensor.device_info["translation_placeholders"] == {"client_name": expected_name}
        actual_ids.add(sensor.unique_id)

    assert actual_ids == {"stable-entry_unknown"}


def test_entity_ids_are_english_and_ignore_the_language() -> None:
    entry = SimpleNamespace(
        unique_id="stable-entry", entry_id="entry-id", title="Pi-hole 192.0.2.2", data={}
    )

    assert {
        canonical_entity_id(entry, domain, f"stable-entry_192.0.2.10{suffix}")
        for domain, suffix in (
            ("sensor", ""),
            ("sensor", "_queries_last_minute"),
            ("sensor", "_allowed_v1"),
            ("sensor", "_blocked_v1"),
            ("sensor", "_provisional_v1"),
            ("binary_sensor", "_unusual_activity"),
        )
    } == {
        "sensor.phe_192_0_2_10_dns_queries",
        "sensor.phe_192_0_2_10_queries_last_minute",
        "sensor.phe_192_0_2_10_allowed_v1",
        "sensor.phe_192_0_2_10_blocked_v1",
        "sensor.phe_192_0_2_10_provisional_v1",
        "binary_sensor.phe_192_0_2_10_unusual_activity",
    }
    assert {description.key: diagnostic_entity_id(entry, description.key)
            for description in DIAGNOSTIC_DESCRIPTIONS} == {
        "communication_status": "sensor.pi_hole_192_0_2_2_diagnostics_communication_status",
        "last_successful_read": "sensor.pi_hole_192_0_2_2_diagnostics_last_successful_read",
        "last_query_observed": "sensor.pi_hole_192_0_2_2_diagnostics_last_query_observed",
        "coverage": "sensor.pi_hole_192_0_2_2_diagnostics_ingestion_coverage",
    }
