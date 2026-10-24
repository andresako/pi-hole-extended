"""Entity registry callbacks must run on Home Assistant's event loop."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler

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

    legacy = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "legacy-pihole-endpoint_192.0.2.11_allowed",
        config_entry=entry,
        suggested_object_id="my_custom_allowed_history",
        original_name="Kitchen allowed history",
        translation_key="legacy_allowed",
    )

    assert await async_setup_entry(hass, entry)

    archived = registry.async_get(legacy.entity_id)
    assert archived is not None
    assert archived.unique_id == legacy.unique_id
    assert archived.original_name == "Kitchen allowed history"
    assert archived.disabled_by is RegistryEntryDisabler.INTEGRATION
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


@pytest.mark.asyncio
async def test_retired_class_entities_are_archived_without_rewriting_registry_state(
    tmp_path,
) -> None:
    """Archive only active legacy counters for this entry and preserve their IDs."""
    from custom_components.pihole_queries import _archive_legacy_class_sensors

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
        entry_id="legacy-entry-a",
        unique_id="http://pihole.example.test:80",
        domain=DOMAIN,
        data={
            CONF_SOURCE_ID: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            CONF_SOURCE_GENERATION: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        },
        pref_disable_new_entities=False,
    )
    other_entry = SimpleNamespace(
        entry_id="legacy-entry-b",
        unique_id="http://pihole.example.test:81",
        data={CONF_SOURCE_GENERATION: LEGACY_SOURCE_GENERATION},
        pref_disable_new_entities=False,
        disabled_by=None,
    )
    hass.config_entries = SimpleNamespace(
        async_get_entry=lambda entry_id: {
            entry.entry_id: entry,
            other_entry.entry_id: other_entry,
        }.get(entry_id)
    )
    entry.disabled_by = None
    devices = dr.async_get(hass)
    loaded_event = getattr(devices, "_loaded_event", None)
    if hasattr(devices, "async_load") and not (
        loaded_event is not None and loaded_event.is_set()
    ):
        await devices.async_load()
    client_device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_192.0.2.10")},
        name="Kitchen DNS client",
        manufacturer="Pi-hole",
        model="DNS client",
    )

    allowed = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.data[CONF_SOURCE_ID]}_{entry.data[CONF_SOURCE_GENERATION]}_192.0.2.10_allowed",
        config_entry=entry,
        suggested_object_id="my_custom_allowed_history",
        original_name="Kitchen allowed history",
        translation_key="legacy_allowed",
        device_id=client_device.id,
    )
    allowed = registry.async_update_entity(
        allowed.entity_id, new_entity_id="sensor.pihole_favorite_allowed"
    )
    blocked = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.data[CONF_SOURCE_ID]}_{entry.data[CONF_SOURCE_GENERATION]}_192.0.2.10_blocked",
        config_entry=entry,
        suggested_object_id="client_blocked_history",
        original_name="Kitchen blocked history",
        translation_key="legacy_blocked",
    )
    registry.async_update_entity(
        blocked.entity_id, disabled_by=RegistryEntryDisabler.USER
    )
    total = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.data[CONF_SOURCE_ID]}_{entry.data[CONF_SOURCE_GENERATION]}_192.0.2.10",
        config_entry=entry,
    )
    old_minute = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.data[CONF_SOURCE_ID]}_{entry.data[CONF_SOURCE_GENERATION]}_192.0.2.10_queries_last_minute",
        config_entry=entry,
        suggested_object_id="favorite_old_minute_count",
        original_name="Consultas (último minuto)",
        translation_key=None,
        device_id=client_device.id,
    )
    old_minute = registry.async_update_entity(
        old_minute.entity_id, new_entity_id="sensor.custom_old_minute_count"
    )
    hass.states.async_set(old_minute.entity_id, "7")
    old_allowed_rate = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.data[CONF_SOURCE_ID]}_{entry.data[CONF_SOURCE_GENERATION]}_192.0.2.10_allowed_last_minute",
        config_entry=entry,
        suggested_object_id="favorite_old_allowed_rate",
        original_name="Permitidas (último minuto)",
        translation_key=None,
        device_id=client_device.id,
    )
    old_allowed_rate = registry.async_update_entity(
        old_allowed_rate.entity_id, new_entity_id="sensor.custom_old_allowed_rate"
    )
    hass.states.async_set(old_allowed_rate.entity_id, "5")
    old_blocked_rate = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.data[CONF_SOURCE_ID]}_{entry.data[CONF_SOURCE_GENERATION]}_192.0.2.10_blocked_last_minute",
        config_entry=entry,
        original_name="Bloqueadas (último minuto)",
        translation_key=None,
    )
    registry.async_update_entity(
        old_blocked_rate.entity_id, disabled_by=RegistryEntryDisabler.USER
    )
    classified = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.data[CONF_SOURCE_ID]}_{entry.data[CONF_SOURCE_GENERATION]}_192.0.2.10_allowed_v1",
        config_entry=entry,
    )
    ambiguous_total = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.data[CONF_SOURCE_ID]}_{entry.data[CONF_SOURCE_GENERATION]}_my_allowed",
        config_entry=entry,
        translation_key="queries",
    )
    second_instance = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{other_entry.unique_id}_192.0.2.10_allowed",
        config_entry=other_entry,
    )

    hass.states.async_set(allowed.entity_id, "31")
    allowed_entity_id = allowed.entity_id
    allowed_unique_id = allowed.unique_id
    blocked_entity_id = blocked.entity_id
    blocked_unique_id = blocked.unique_id
    old_minute_entity_id = old_minute.entity_id
    old_minute_unique_id = old_minute.unique_id
    old_allowed_rate_entity_id = old_allowed_rate.entity_id
    old_allowed_rate_unique_id = old_allowed_rate.unique_id
    _archive_legacy_class_sensors(hass, entry)
    _archive_legacy_class_sensors(hass, entry)

    archived_allowed = registry.async_get(allowed_entity_id)
    user_disabled_blocked = registry.async_get(blocked_entity_id)
    archived_minute = registry.async_get(old_minute_entity_id)
    archived_allowed_rate = registry.async_get(old_allowed_rate_entity_id)
    user_disabled_blocked_rate = registry.async_get(old_blocked_rate.entity_id)
    assert archived_allowed is not None
    assert archived_allowed.unique_id == allowed_unique_id
    assert archived_allowed.original_name == "Kitchen allowed history"
    assert archived_allowed.disabled_by is RegistryEntryDisabler.INTEGRATION
    assert archived_allowed.device_id == client_device.id
    assert user_disabled_blocked is not None
    assert user_disabled_blocked.unique_id == blocked_unique_id
    assert user_disabled_blocked.original_name == "Kitchen blocked history"
    assert user_disabled_blocked.disabled_by is RegistryEntryDisabler.USER
    assert archived_minute is not None
    assert archived_minute.unique_id == old_minute_unique_id
    assert archived_minute.entity_id == "sensor.custom_old_minute_count"
    assert archived_minute.original_name == "Consultas (último minuto)"
    assert archived_minute.disabled_by is None
    assert archived_allowed_rate is not None
    assert archived_allowed_rate.unique_id == old_allowed_rate_unique_id
    assert archived_allowed_rate.entity_id == "sensor.custom_old_allowed_rate"
    assert archived_allowed_rate.original_name == "Permitidas (último minuto)"
    assert archived_allowed_rate.disabled_by is RegistryEntryDisabler.INTEGRATION
    assert user_disabled_blocked_rate is not None
    assert user_disabled_blocked_rate.disabled_by is RegistryEntryDisabler.USER
    assert registry.async_get(total.entity_id).disabled_by is None
    assert registry.async_get(classified.entity_id).disabled_by is None
    assert registry.async_get(ambiguous_total.entity_id).disabled_by is None
    assert registry.async_get(second_instance.entity_id).disabled_by is None
    assert dr.async_get(hass).async_get(client_device.id).name == "Kitchen DNS client"
    assert hass.states.get(allowed_entity_id).state == "31"
    assert hass.states.get(old_minute_entity_id).state == "7"
    assert hass.states.get(old_allowed_rate_entity_id).state == "5"
    await hass.async_stop()


@pytest.mark.asyncio
async def test_pre_translation_legacy_counters_are_archived_by_original_name(tmp_path) -> None:
    """Retire 0.6.0 registry entries without translation keys, preserving names."""
    from custom_components.pihole_queries import _archive_legacy_class_sensors

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
        entry_id="pihole-06-entry",
        unique_id="legacy-pihole-endpoint",
        domain=DOMAIN,
        disabled_by=None,
        pref_disable_new_entities=False,
        data={CONF_SOURCE_GENERATION: LEGACY_SOURCE_GENERATION},
    )
    hass.config_entries = SimpleNamespace(
        async_get_entry=lambda entry_id: entry if entry_id == entry.entry_id else None
    )
    devices = dr.async_get(hass)
    loaded_event = getattr(devices, "_loaded_event", None)
    if hasattr(devices, "async_load") and not (
        loaded_event is not None and loaded_event.is_set()
    ):
        await devices.async_load()
    allowed_device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "pihole-06-entry_living_room")},
        name="Living Room (192.0.2.10)",
        manufacturer="Pi-hole",
        model="DNS client",
    )
    total_device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "pihole-06-entry_guest_allowed")},
        name="Custom allowed client device",
        manufacturer="Pi-hole",
        model="DNS client",
    )

    allowed = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "legacy-pihole-endpoint_living_room_allowed",
        config_entry=entry,
        suggested_object_id="old_allowed_entity",
        original_name="Permitidas",
        translation_key=None,
        device_id=allowed_device.id,
    )
    allowed = registry.async_update_entity(
        allowed.entity_id,
        new_entity_id="sensor.custom_allowed_history",
        name="My custom allow history",
    )
    blocked = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "legacy-pihole-endpoint_living_room_blocked",
        config_entry=entry,
        suggested_object_id="old_blocked_entity",
        original_name="Bloqueadas",
        translation_key=None,
        device_id=allowed_device.id,
    )
    ambiguous_total = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "legacy-pihole-endpoint_guest_allowed",
        config_entry=entry,
        suggested_object_id="total_client_named_allowed",
        original_name="Consultas DNS",
        translation_key=None,
        device_id=total_device.id,
    )

    _archive_legacy_class_sensors(hass, entry)

    archived_allowed = registry.async_get(allowed.entity_id)
    archived_blocked = registry.async_get(blocked.entity_id)
    still_active_total = registry.async_get(ambiguous_total.entity_id)
    assert archived_allowed is not None
    assert archived_allowed.unique_id == "legacy-pihole-endpoint_living_room_allowed"
    assert archived_allowed.entity_id == "sensor.custom_allowed_history"
    assert archived_allowed.name == "My custom allow history"
    assert archived_allowed.original_name == "Permitidas"
    assert archived_allowed.translation_key is None
    assert archived_allowed.disabled_by is RegistryEntryDisabler.INTEGRATION
    assert archived_allowed.device_id == allowed_device.id
    assert archived_blocked is not None
    assert archived_blocked.disabled_by is RegistryEntryDisabler.INTEGRATION
    assert archived_blocked.original_name == "Bloqueadas"
    assert still_active_total is not None
    assert still_active_total.translation_key is None
    assert still_active_total.original_name == "Consultas DNS"
    assert still_active_total.disabled_by is None
    assert devices.async_get(allowed_device.id).name == "Living Room (192.0.2.10)"
    assert devices.async_get(total_device.id).name == "Custom allowed client device"
    await hass.async_stop()


@pytest.mark.asyncio
async def test_new_sensor_setup_adds_restored_minute_sensor_and_four_diagnostics(tmp_path) -> None:
    """New entries publish five client sensors and four diagnostics."""
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(
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
