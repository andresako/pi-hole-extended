"""Entity registry callbacks must run on Home Assistant's event loop."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.pihole_queries import async_setup_entry
from custom_components.pihole_queries.const import (
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    DOMAIN,
    LEGACY_SOURCE_GENERATION,
)


@pytest.mark.asyncio
async def test_new_registry_entity_is_normalized_on_the_event_loop(
    tmp_path, monkeypatch
) -> None:
    """Creating a real registry entity dispatches normalization safely."""
    import custom_components.pihole_queries as integration

    hass = HomeAssistant(str(tmp_path))
    hass.data[DOMAIN] = {}
    hass.config_entries = SimpleNamespace(
        async_forward_entry_setups=AsyncMock(return_value=None)
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
        data={
            CONF_SOURCE_ID: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            CONF_SOURCE_GENERATION: LEGACY_SOURCE_GENERATION,
        },
        options={},
        async_on_unload=unload_callbacks.append,
        add_update_listener=lambda _listener: lambda: None,
    )

    assert await async_setup_entry(hass, entry)

    registry = er.async_get(hass)
    await registry.async_load()
    created = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "legacy-pihole-endpoint_192.0.2.10_allowed",
        config_entry=entry,
    )

    normalized = registry.async_get("sensor.phe_192_0_2_10_permitidas")
    assert normalized is not None
    assert normalized.unique_id == created.unique_id
    assert len(unload_callbacks) == 2
