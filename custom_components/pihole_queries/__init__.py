"""Pi-hole v6 DNS query history integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration

from .api import PiHoleClient
from .const import (
    CARD_FILENAME,
    CARD_URL_BASE,
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    DOMAIN,
    FIRST_SOURCE_GENERATION,
    PLATFORMS,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
    TRANSLATIONS_URL_BASE,
)
from .coordinator import PiHoleQueryCoordinator
from .entity_ids import (
    available_entity_id as _available_entity_id,
)
from .entity_ids import (
    canonical_entity_id,
    client_device_identifier,
)
from .store import _IdentityStore
from .websocket import async_register as websocket_api_register

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _valid_uuid(value: Any) -> str | None:
    """Return a canonical UUID string, if the stored value is valid."""
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _identity_storage_key(source_id: str, generation: str) -> str:
    """Build a Store key scoped to one Pi-hole source generation."""
    return f"{STORAGE_KEY_PREFIX}_{source_id}_{generation}"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration domain."""
    hass.data.setdefault(DOMAIN, {})
    websocket_api_register(hass)
    await _async_register_frontend(hass)
    return True


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the activity card and load it in every dashboard."""
    if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
        return
    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(CARD_URL_BASE, str(Path(__file__).parent / "frontend"), False),
            # The card reads its strings from the integration's own catalogs.
            StaticPathConfig(
                TRANSLATIONS_URL_BASE, str(Path(__file__).parent / "translations"), False
            ),
        ]
    )
    url = f"{CARD_URL_BASE}/{CARD_FILENAME}"
    add_extra_js_url(hass, f"{url}?v={version}")


def _canonical_entity_id(entry: ConfigEntry, entity: er.RegistryEntry) -> str | None:
    """Build the stable recorder-friendly ID for a Pi-hole client metric."""
    return canonical_entity_id(entry, entity.domain, entity.unique_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a configured Pi-hole and begin tracking new DNS queries."""
    client = PiHoleClient(async_get_clientsession(hass), {**entry.data, **entry.options})
    source_id = _valid_uuid(entry.data.get(CONF_SOURCE_ID))
    generation_value = entry.data.get(CONF_SOURCE_GENERATION)
    generation = (
        generation_value
        if generation_value == FIRST_SOURCE_GENERATION
        else _valid_uuid(generation_value)
    )
    if source_id is None or generation is None:
        await client.async_close()
        raise HomeAssistantError("The entry has no valid source_id/source_generation identity")
    store = _IdentityStore(
        Store(
            hass,
            STORAGE_VERSION,
            TRANSLATIONS_URL_BASE,
            _identity_storage_key(source_id, generation),
        ),
        source_id,
        generation,
    )
    coordinator = PiHoleQueryCoordinator(hass, entry, client, store)
    try:
        await coordinator.async_load_cursor()
        await coordinator.async_config_entry_first_refresh()
        hass.data[DOMAIN][entry.entry_id] = {"client": client, "coordinator": coordinator}

        registry = er.async_get(hass)

        @callback
        def normalize_entity(event: Any) -> None:
            """Keep IDs under phe_ when a new Pi-hole client is discovered."""
            if event.data.get("action") != "create":
                return
            entity = registry.async_get(event.data.get("entity_id"))
            if (
                entity is None
                or entity.config_entry_id != entry.entry_id
                or entity.platform != DOMAIN
            ):
                return
            target = _canonical_entity_id(entry, entity)
            if target and entity.entity_id != target:
                registry.async_update_entity(
                    entity.entity_id, new_entity_id=_available_entity_id(registry, target)
                )

        entry.async_on_unload(
            hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, normalize_entity)
        )
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry.async_on_unload(entry.add_update_listener(_async_options_updated))
        return True
    except BaseException:
        # A failed first refresh or platform setup must not strand a Pi-hole SID.
        hass.data[DOMAIN].pop(entry.entry_id, None)
        try:
            await coordinator.async_shutdown()
        finally:
            await client.async_close()
        raise


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Let the user remove any client device; it returns if the client queries again."""
    if (DOMAIN, f"{entry.entry_id}_diagnostics") in device_entry.identifiers:
        return False
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None:
        # The client's counters can't be dropped now and would restore the device.
        return False
    coordinator = runtime["coordinator"]
    for client_key in {*coordinator.client_data, *coordinator.aggregate_pending}:
        if client_device_identifier(entry, client_key) in device_entry.identifiers:
            return await coordinator.async_forget_client(client_key)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload so a changed polling interval takes effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop polling and close the Pi-hole session when unloading."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = hass.data[DOMAIN].pop(entry.entry_id)
    try:
        await runtime["coordinator"].async_shutdown()
    finally:
        await runtime["client"].async_close()
    return True
