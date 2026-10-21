"""Pi-hole v6 DNS query history integration."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify

from .api import PiHoleClient
from .const import DOMAIN, PLATFORMS, STORAGE_KEY_PREFIX, STORAGE_VERSION
from .coordinator import PiHoleQueryCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration domain."""
    hass.data.setdefault(DOMAIN, {})
    return True


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
        # Entity IDs created before 0.5.2 included the client's friendly name
        # before ``phe_``. Normalize every metric from its stable unique ID so
        # Recorder can use one simple ``sensor.phe_*`` include rule.
        metric_suffixes = {
            "_allowed": "permitidas",
            "_blocked": "bloqueadas",
            "_query_rate": "consultas_por_segundo",
            "_allowed_rate": "permitidas_por_segundo",
            "_blocked_rate": "bloqueadas_por_segundo",
            "": "consultas_dns",
        }
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain != "sensor":
                continue
            suffix = next(
                (suffix for suffix in metric_suffixes if entity.unique_id.endswith(suffix)),
                None,
            )
            if suffix is None:
                continue
            unique_prefix = f"{entry.unique_id or entry.entry_id}_"
            if not entity.unique_id.startswith(unique_prefix):
                continue
            client_key = entity.unique_id.removeprefix(unique_prefix).removesuffix(suffix)
            client_id = slugify(client_key)
            target = f"sensor.phe_{client_id}_{metric_suffixes[suffix]}"
            if entity.entity_id == target:
                continue
            # Preserve existing history by renaming the registry entry; do not
            # remove/recreate entities or alter Recorder's database.
            registry.async_update_entity(
                entity.entity_id, new_entity_id=_available_entity_id(registry, target)
            )

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
        # Also normalize sensors whose IDs were generated from the device name
        # before the entity started setting its object ID explicitly.
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            target = _canonical_entity_id(entry, entity)
            if target and entity.entity_id != target:
                registry.async_update_entity(
                    entity.entity_id, new_entity_id=_available_entity_id(registry, target)
                )

        hass.config_entries.async_update_entry(entry, version=7)
    return entry.version == 7


def _client_id_from_device_name(device_name: str) -> str:
    """Use the client IP in entity IDs when it is part of the device name."""
    match = re.fullmatch(r"(.+) \(([^()]*)\)", device_name)
    if match:
        client_name, client_ip = match.groups()
        if client_ip != "Desconocida":
            return client_ip
        return client_name
    return device_name


def _available_entity_id(registry: er.EntityRegistry, entity_id: str) -> str:
    """Return an unused entity ID, adding a numeric suffix if necessary."""
    base_entity_id = entity_id
    collision = 2
    while registry.async_get(entity_id) is not None:
        entity_id = f"{base_entity_id}_{collision}"
        collision += 1
    return entity_id


def _canonical_entity_id(entry: ConfigEntry, entity: er.RegistryEntry) -> str | None:
    """Build the stable recorder-friendly ID for a Pi-hole client metric."""
    if entity.domain != "sensor":
        return None
    metric_suffixes = {
        "_allowed": "permitidas",
        "_blocked": "bloqueadas",
        "_queries_last_minute": "consultas_en_el_ultimo_minuto",
        "_allowed_last_minute": "permitidas_en_el_ultimo_minuto",
        "_blocked_last_minute": "bloqueadas_en_el_ultimo_minuto",
        "": "consultas_dns",
    }
    suffix = next(
        (suffix for suffix in metric_suffixes if entity.unique_id.endswith(suffix)),
        None,
    )
    unique_prefix = f"{entry.unique_id or entry.entry_id}_"
    if suffix is None or not entity.unique_id.startswith(unique_prefix):
        return None
    client_key = entity.unique_id.removeprefix(unique_prefix).removesuffix(suffix)
    return f"sensor.phe_{slugify(client_key)}_{metric_suffixes[suffix]}"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a configured Pi-hole and begin tracking new DNS queries."""
    client = PiHoleClient(async_get_clientsession(hass), {**entry.data, **entry.options})
    store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry.entry_id}")
    coordinator = PiHoleQueryCoordinator(hass, entry, client, store)
    try:
        await coordinator.async_load_cursor()
        await coordinator.async_config_entry_first_refresh()
        hass.data[DOMAIN][entry.entry_id] = {"client": client, "coordinator": coordinator}

        registry = er.async_get(hass)

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
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            target = _canonical_entity_id(entry, entity)
            if target and entity.entity_id != target:
                registry.async_update_entity(
                    entity.entity_id, new_entity_id=_available_entity_id(registry, target)
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
