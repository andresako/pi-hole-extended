"""Entity registry callbacks must run on Home Assistant's event loop."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.pihole_queries import async_setup_entry
from custom_components.pihole_queries.const import (
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    DOMAIN,
    LEGACY_SOURCE_GENERATION,
)
from custom_components.pihole_queries.sensor import (
    DIAGNOSTIC_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
)
from custom_components.pihole_queries.sensor import (
    async_setup_entry as async_setup_sensor_entry,
)


@pytest.mark.asyncio
async def test_new_registry_entity_is_normalized_on_the_event_loop(
    tmp_path, monkeypatch
) -> None:
    """Creating a real registry entity dispatches normalization safely."""
    import custom_components.pihole_queries as integration

    hass = HomeAssistant(str(tmp_path))
    hass.data[DOMAIN] = {}
    if hasattr(dr, "async_setup"):
        dr.async_setup(hass)
        await dr.async_load(hass)
    else:
        dr.async_get(hass)
    registry = er.async_get(hass)
    await registry.async_load()
    hass.config_entries = SimpleNamespace(
        async_forward_entry_setups=AsyncMock(return_value=None),
        async_get_entry=lambda entry_id: entry if entry_id == entry.entry_id else None,
    )

    class Client:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def async_close(self) -> None:
            pass

    class Coordinator:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def async_load_cursor(self) -> None:
            pass

        async def async_config_entry_first_refresh(self) -> None:
            pass

        async def async_shutdown(self) -> None:
            pass

    monkeypatch.setattr(integration, "PiHoleClient", Client)
    monkeypatch.setattr(integration, "PiHoleQueryCoordinator", Coordinator)
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: object())
    monkeypatch.setattr(integration, "_IdentityStore", lambda *_args, **_kwargs: object())

    unload_callbacks = []
    entry = SimpleNamespace(
        entry_id="registry-callback-test",
        unique_id="legacy-pihole-endpoint",
        title="Pi-hole test",
        pref_disable_new_entities=False,
        disabled_by=None,
        data={
            CONF_SOURCE_ID: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            CONF_SOURCE_GENERATION: LEGACY_SOURCE_GENERATION,
        },
        options={},
        async_on_unload=unload_callbacks.append,
        add_update_listener=lambda _listener: lambda: None,
    )

    assert await async_setup_entry(hass, entry)

    created = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "legacy-pihole-endpoint_192.0.2.10_allowed_v1",
        config_entry=entry,
    )

    normalized = registry.async_get("sensor.phe_192_0_2_10_allowed_v1")
    assert normalized is not None
    assert normalized.unique_id == created.unique_id
    assert len(unload_callbacks) == 2


@pytest.mark.asyncio
async def test_new_sensor_setup_adds_restored_minute_sensor_and_four_diagnostics(tmp_path) -> None:
    """New entries publish five client sensors and four diagnostics."""
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(
        title="Pi-hole test",
        entry_id="new-install-entry",
        unique_id="new-install-endpoint",
        data={CONF_SOURCE_GENERATION: LEGACY_SOURCE_GENERATION},
        async_on_unload=lambda _callback: None,
    )

    class Coordinator:
        client_data = {
            "192.0.2.10": {"client_name": "Synthetic", "client_ip": "192.0.2.10"}
        }

        def async_add_listener(self, _listener):
            return lambda: None

        def async_add_client_removed_listener(self, _listener):
            return lambda: None

    coordinator = Coordinator()
    coordinator.hass = hass
    hass.data[DOMAIN] = {entry.entry_id: {"coordinator": coordinator}}
    added = []
    await async_setup_sensor_entry(hass, entry, added.extend)

    client_keys = {entity.entity_description.key for entity in added[:5]}
    diagnostic_keys = {entity.entity_description.key for entity in added[5:]}
    assert client_keys == {description.key for description in SENSOR_DESCRIPTIONS} == {
        "queries",
        "queries_last_minute",
        "allowed_v1",
        "blocked_v1",
        "provisional_v1",
    }
    assert diagnostic_keys == {description.key for description in DIAGNOSTIC_DESCRIPTIONS}
    assert len(added) == 9
    minute_sensor = next(
        entity for entity in added if entity.entity_description.key == "queries_last_minute"
    )
    assert minute_sensor.unique_id == "new-install-endpoint_192.0.2.10_queries_last_minute"
