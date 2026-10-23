"""Entity and device names stay localized without changing registry identity."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from custom_components.pihole_queries.const import DOMAIN
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


def test_fallback_entity_and_device_names_are_spanish() -> None:
    fallback = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    spanish = json.loads(
        (COMPONENT / "translations" / "es.json").read_text(encoding="utf-8")
    )

    # Home Assistant uses strings.json when no locale-specific catalog applies.
    assert fallback["entity"] == spanish["entity"]
    assert fallback["device"] == spanish["device"]


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


def test_translation_changes_do_not_change_client_unique_ids() -> None:
    entry = SimpleNamespace(unique_id="stable-entry", entry_id="entry-id")
    expected = {
        "queries": "stable-entry_192.0.2.10",
        "allowed": "stable-entry_192.0.2.10_allowed",
        "blocked": "stable-entry_192.0.2.10_blocked",
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
    ):
        coordinator = SimpleNamespace(
            client_data={"unknown": {"client_name": "Cliente desconocido"}},
            hass=SimpleNamespace(config=SimpleNamespace(language=language)),
        )
        sensor = PiHoleClientSensor(coordinator, entry, "unknown", description)

        assert sensor.device_info["translation_key"] == "client_device"
        assert sensor.device_info["translation_placeholders"] == {"client_name": expected_name}
        actual_ids.add(sensor.unique_id)

    assert actual_ids == {"stable-entry_unknown"}
