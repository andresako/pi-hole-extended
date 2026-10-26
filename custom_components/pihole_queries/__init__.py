"""Pi-hole v6 DNS query history integration."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration
from homeassistant.util import slugify

from .api import PiHoleClient
from .const import (
    CARD_FILENAME,
    CARD_URL_BASE,
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    DOMAIN,
    LEGACY_SOURCE_GENERATION,
    LEGACY_STORAGE_VERSION,
    PLATFORMS,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)
from .coordinator import PiHoleQueryCoordinator
from .entity_ids import (
    available_entity_id as _available_entity_id,
)
from .entity_ids import (
    canonical_entity_id,
)
from .entity_ids import (
    entity_unique_prefix as _entity_unique_prefix,
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




def _archive_legacy_class_sensors(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Disable retired legacy class counters for this source generation."""
    prefix = f"{_entity_unique_prefix(entry)}_"
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity.platform != DOMAIN
            or entity.domain != "sensor"
            or not entity.unique_id.startswith(prefix)
            or not _is_retired_legacy_class_sensor(entity)
            or entity.disabled_by is not None
        ):
            continue
        registry.async_update_entity(
            entity.entity_id,
            disabled_by=RegistryEntryDisabler.INTEGRATION,
        )


def _is_retired_legacy_class_sensor(entity: er.RegistryEntry) -> bool:
    """Match only the two known 0.6 class descriptors, never other entities."""
    legacy_classes = (
        entity.unique_id.endswith("_allowed")
        and (
            entity.translation_key == "legacy_allowed"
            or (
                entity.translation_key is None
                and entity.original_name == "Permitidas"
            )
        )
    ) or (
        entity.unique_id.endswith("_blocked")
        and (
            entity.translation_key == "legacy_blocked"
            or (
                entity.translation_key is None
                and entity.original_name == "Bloqueadas"
            )
        )
    )
    legacy_class_windows = (
        entity.unique_id.endswith("_allowed_last_minute")
        and (
            entity.translation_key == "allowed_last_minute"
            or (
                entity.translation_key is None
                and entity.original_name == "Permitidas (último minuto)"
            )
        )
    ) or (
        entity.unique_id.endswith("_blocked_last_minute")
        and (
            entity.translation_key == "blocked_last_minute"
            or (
                entity.translation_key is None
                and entity.original_name == "Bloqueadas (último minuto)"
            )
        )
    )
    return legacy_classes or legacy_class_windows


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
    # Serve the whole folder: the card script plus its translations/*.json files.
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_BASE, str(Path(__file__).parent / "frontend"), False)]
    )
    url = f"{CARD_URL_BASE}/{CARD_FILENAME}"
    add_extra_js_url(hass, f"{url}?v={version}")


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove obsolete sensors and assign concise, integration-prefixed IDs."""
    registry = er.async_get(hass)
    metric_names = {
        "consultas_dns": "Consultas DNS",
        "permitidas": "Permitidas",
        "bloqueadas": "Bloqueadas",
        "consultas_por_segundo": "Consultas por segundo",
        "permitidas_por_segundo": "Permitidas por segundo",
        "bloqueadas_por_segundo": "Bloqueadas por segundo",
    }
    if entry.version < 2:
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.unique_id.endswith(("_last_domain", "_last_result")):
                registry.async_remove(entity.entity_id)
        hass.config_entries.async_update_entry(entry, version=2)

    if entry.version < 3:
        device_registry = dr.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain != "sensor" or not entity.device_id:
                continue
            if entity.entity_id.startswith("sensor.phe_"):
                continue
            device = device_registry.async_get(entity.device_id)
            if device is None or device.name_by_user:
                continue
            metric = next(
                (
                    name
                    for suffix, name in metric_names.items()
                    if entity.entity_id.endswith(f"_{suffix}")
                ),
                None,
            )
            if metric is None:
                continue

            # Only migrate Home Assistant's default IDs. Leave any user-customized
            # entity ID untouched.
            old_default_name = slugify(f"{device.name} {metric}")
            old_default = f"sensor.{old_default_name}"
            if entity.entity_id != old_default:
                continue

            client_name = device.name
            match = re.fullmatch(r"(.+) \(([^()]*)\)", client_name)
            if match:
                client_name, client_ip = match.groups()
                if client_ip != "Desconocida":
                    client_name = client_ip
            new_object_id = slugify(f"PHE {client_name} {metric}")
            new_entity_id = _available_entity_id(registry, f"sensor.{new_object_id}")
            registry.async_update_entity(entity.entity_id, new_entity_id=new_entity_id)

        hass.config_entries.async_update_entry(entry, version=3)

    if entry.version < 4:
        device_registry = dr.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            prefix = "sensor.pi_hole_extended_"
            if not entity.entity_id.startswith(prefix) or not entity.device_id:
                continue
            device = device_registry.async_get(entity.device_id)
            if device is None or device.name_by_user:
                continue
            metric = next(
                (
                    name
                    for suffix, name in metric_names.items()
                    if entity.entity_id.endswith(f"_{suffix}")
                ),
                None,
            )
            if metric is None:
                continue
            client_id = _client_id_from_device_name(device.name)
            old_default = f"sensor.{slugify(f'Pi-hole Extended {client_id} {metric}')}"
            if entity.entity_id != old_default:
                continue
            new_entity_id = f"sensor.phe_{entity.entity_id.removeprefix(prefix)}"
            new_entity_id = _available_entity_id(registry, new_entity_id)
            registry.async_update_entity(entity.entity_id, new_entity_id=new_entity_id)

        hass.config_entries.async_update_entry(entry, version=4)

    if entry.version < 5:
        # Preserve all registered IDs. There is no reliable marker that
        # distinguishes a former default ID from a user customization.
        hass.config_entries.async_update_entry(entry, version=5)

    if entry.version < 6:
        # The one-second rate sensors are replaced by rolling 60-second
        # counts. Remove only those obsolete registry entries; cumulative
        # query counters and the Recorder database are kept intact.
        old_rate_suffixes = ("_query_rate", "_allowed_rate", "_blocked_rate")
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain == "sensor" and entity.unique_id.endswith(old_rate_suffixes):
                registry.async_remove(entity.entity_id)
        hass.config_entries.async_update_entry(entry, version=6)

    if entry.version < 7:
        # Keep the Home Assistant registry authoritative for existing IDs.
        hass.config_entries.async_update_entry(entry, version=7)
    if entry.version < 8:
        data = dict(entry.data)
        data[CONF_SOURCE_ID] = _valid_uuid(data.get(CONF_SOURCE_ID)) or str(uuid4())
        generation = data.get(CONF_SOURCE_GENERATION)
        data[CONF_SOURCE_GENERATION] = (
            generation
            if generation == LEGACY_SOURCE_GENERATION or _valid_uuid(generation)
            else LEGACY_SOURCE_GENERATION
        )
        hass.config_entries.async_update_entry(entry, data=data, version=8)
    return entry.version == 8


def _client_id_from_device_name(device_name: str) -> str:
    """Use the client IP in entity IDs when it is part of the device name."""
    match = re.fullmatch(r"(.+) \(([^()]*)\)", device_name)
    if match:
        client_name, client_ip = match.groups()
        if client_ip != "Desconocida":
            return client_ip
        return client_name
    return device_name




def _canonical_entity_id(entry: ConfigEntry, entity: er.RegistryEntry) -> str | None:
    """Build the stable recorder-friendly ID for a Pi-hole client metric."""
    return canonical_entity_id(entry, entity.domain, entity.unique_id)




async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a configured Pi-hole and begin tracking new DNS queries."""
    _archive_legacy_class_sensors(hass, entry)
    client = PiHoleClient(async_get_clientsession(hass), {**entry.data, **entry.options})
    source_id = _valid_uuid(entry.data.get(CONF_SOURCE_ID))
    generation_value = entry.data.get(CONF_SOURCE_GENERATION)
    generation = (
        generation_value
        if generation_value == LEGACY_SOURCE_GENERATION
        else _valid_uuid(generation_value)
    )
    if source_id is None or generation is None:
        await client.async_close()
        raise HomeAssistantError("La entrada no contiene identidad source_id/source_generation válida")
    store = _IdentityStore(
        Store(
            hass,
            STORAGE_VERSION,
            _identity_storage_key(source_id, generation),
        ),
        Store(
            hass,
            LEGACY_STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}_{entry.entry_id}",
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
