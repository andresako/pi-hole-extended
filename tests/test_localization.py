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


def test_shipped_translations_have_the_same_shape() -> None:
    catalogs = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (
            COMPONENT / "translations" / "en.json",
            COMPONENT / "translations" / "es.json",
        )
    ]

    assert _keys(catalogs[0]) == _keys(catalogs[1])


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

    assert translations["en"]["entity"]["sensor"]["allowed"]["name"] == "Allowed queries"
    assert translations["en"]["entity"]["sensor"]["blocked"]["name"] == "Blocked queries"
    assert translations["en"]["entity"]["sensor"]["provisional"]["name"] == "Provisional queries"


def test_translation_changes_do_not_change_client_unique_ids() -> None:
    entry = SimpleNamespace(
        unique_id="stable-entry",
        entry_id="entry-id",
        data={"source_id": "source", "source_generation": "first"},
    )
    expected = {
        "queries": "source_first_192.0.2.10",
        "queries_last_minute": "source_first_192.0.2.10_queries_last_minute",
        "allowed": "source_first_192.0.2.10_allowed",
        "blocked": "source_first_192.0.2.10_blocked",
        "provisional": "source_first_192.0.2.10_provisional",
    }

    for description in SENSOR_DESCRIPTIONS:
        assert (
            _client_entity_unique_id(entry, "192.0.2.10", description.key)
            == expected[description.key]
        )


def test_unknown_client_device_fallback_uses_home_assistant_language() -> None:
    entry = SimpleNamespace(
        unique_id="stable-entry",
        entry_id="entry-id",
        data={"source_id": "source", "source_generation": "first"},
    )
    description = SENSOR_DESCRIPTIONS[0]
    actual_ids = set()

    for language in ("en-US", "es-ES", "fr"):
        coordinator = SimpleNamespace(
            client_data={"unknown": {"client_name": "Unknown client"}},
            hass=SimpleNamespace(config=SimpleNamespace(language=language)),
        )
        sensor = PiHoleClientSensor(coordinator, entry, "unknown", description)

        assert sensor.device_info["translation_key"] == "unknown_client"
        actual_ids.add(sensor.unique_id)

    assert actual_ids == {"source_first_unknown"}


def test_entity_ids_are_english_and_ignore_the_language() -> None:
    entry = SimpleNamespace(
        unique_id="stable-entry",
        entry_id="entry-id",
        title="Pi-hole 192.0.2.2",
        data={"source_id": "source", "source_generation": "first"},
    )

    assert {
        canonical_entity_id(entry, domain, f"source_first_192.0.2.10{suffix}")
        for domain, suffix in (
            ("sensor", ""),
            ("sensor", "_queries_last_minute"),
            ("sensor", "_allowed"),
            ("sensor", "_blocked"),
            ("sensor", "_provisional"),
            ("binary_sensor", "_unusual_activity"),
        )
    } == {
        "sensor.phe_192_0_2_10_dns_queries",
        "sensor.phe_192_0_2_10_queries_last_minute",
        "sensor.phe_192_0_2_10_allowed",
        "sensor.phe_192_0_2_10_blocked",
        "sensor.phe_192_0_2_10_provisional",
        "binary_sensor.phe_192_0_2_10_unusual_activity",
    }
    assert {
        description.key: diagnostic_entity_id(entry, description.key)
        for description in DIAGNOSTIC_DESCRIPTIONS
    } == {
        "communication_status": "sensor.pi_hole_192_0_2_2_diagnostics_communication_status",
        "last_successful_read": "sensor.pi_hole_192_0_2_2_diagnostics_last_successful_read",
        "last_query_observed": "sensor.pi_hole_192_0_2_2_diagnostics_last_query_observed",
        "coverage": "sensor.pi_hole_192_0_2_2_diagnostics_ingestion_coverage",
    }
