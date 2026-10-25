"""Version-pinned integration checks for the published-to-current migration."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store

from custom_components.pihole_queries import (
    _IdentityStore,
    _identity_storage_key,
    async_migrate_entry,
)
from custom_components.pihole_queries.const import (
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    LEGACY_SOURCE_GENERATION,
    LEGACY_STORAGE_VERSION,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)


@pytest.mark.asyncio
async def test_published_v7_1_entry_and_store_v1_migrate_with_real_ha_store(
    tmp_path,
) -> None:
    """Import v0.6.0 state, preserve its v1 backup, and load v2 after restart."""
    hass = HomeAssistant(str(tmp_path))
    await hass.async_start()

    if hasattr(dr, "async_setup"):
        dr.async_setup(hass)
        await dr.async_load(hass)
    else:
        dr.async_get(hass)
    registry = er.async_get(hass)
    await registry.async_load()

    entry = SimpleNamespace(
        entry_id="published-v0-6-0-entry",
        unique_id="http://pihole.example.test:80",
        version=7,
        minor_version=1,
        data={"host": "pihole.example.test", "port": 80, "scan_interval": 15},
    )

    def update_entry(target, *, data=None, version=None) -> None:
        if data is not None:
            target.data = data
        if version is not None:
            target.version = version

    hass.config_entries = SimpleNamespace(async_update_entry=update_entry)

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 8
    assert entry.minor_version == 1
    assert entry.data[CONF_SOURCE_GENERATION] == LEGACY_SOURCE_GENERATION
    assert entry.data[CONF_SOURCE_ID]

    legacy_payload = {
        "cursor": 9001,
        "clients": {
            "192.0.2.10": {
                "client_name": "Kitchen fixture",
                "client_ip": "192.0.2.10",
                "query_count": 42,
                "allowed_count": 31,
                "blocked_count": 11,
                "last_query": {
                    "id": 9001,
                    "domain": "dns.example.test",
                    "timestamp": 1_760_000_000,
                },
                "recent_queries": [{"id": 9001, "domain": "dns.example.test"}],
            }
        },
    }
    old_store = Store(
        hass,
        LEGACY_STORAGE_VERSION,
        f"{STORAGE_KEY_PREFIX}_{entry.entry_id}",
    )
    await old_store.async_save(legacy_payload)

    current_store = Store(
        hass,
        STORAGE_VERSION,
        _identity_storage_key(
            entry.data[CONF_SOURCE_ID], entry.data[CONF_SOURCE_GENERATION]
        ),
    )
    identity_store = _IdentityStore(
        current_store,
        old_store,
        entry.data[CONF_SOURCE_ID],
        entry.data[CONF_SOURCE_GENERATION],
    )

    imported = await identity_store.async_load()
    assert imported["cursor"] == 9001
    client = imported["clients"]["192.0.2.10"]
    assert client["query_count"] == 42
    assert client["allowed_count"] == 31
    assert client["blocked_count"] == 11
    assert not {"last_query", "last_query_id", "recent_queries", "recent_activity"} & client.keys()
    legacy_after_import = await old_store.async_load()
    assert "dns.example.test" not in repr(legacy_after_import)

    envelope = await current_store.async_load()
    assert envelope == {
        "schema_version": STORAGE_VERSION,
        "source_id": entry.data[CONF_SOURCE_ID],
        "source_generation": LEGACY_SOURCE_GENERATION,
        "data": imported,
    }

    # A new Store object models a Home Assistant restart against the same files.
    restarted = _IdentityStore(
        Store(
            hass,
            STORAGE_VERSION,
            _identity_storage_key(
                entry.data[CONF_SOURCE_ID], entry.data[CONF_SOURCE_GENERATION]
            ),
        ),
        Store(
            hass,
            LEGACY_STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}_{entry.entry_id}",
        ),
        entry.data[CONF_SOURCE_ID],
        entry.data[CONF_SOURCE_GENERATION],
    )
    assert await restarted.async_load() == imported
    old_after_restart = await old_store.async_load()
    assert "dns.example.test" not in repr(old_after_restart)
    await hass.async_stop()
