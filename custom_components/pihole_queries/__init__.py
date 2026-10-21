"""Pi-hole v6 DNS query history integration."""

from __future__ import annotations

import re

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
    if entry.version < 2:
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.unique_id.endswith(("_last_domain", "_last_result")):
                registry.async_remove(entity.entity_id)
        hass.config_entries.async_update_entry(entry, version=2)

    if entry.version < 3:
        device_registry = dr.async_get(hass)
        metric_names = {
            "consultas_dns": "Consultas DNS",
            "permitidas": "Permitidas",
            "bloqueadas": "Bloqueadas",
            "consultas_por_segundo": "Consultas por segundo",
            "permitidas_por_segundo": "Permitidas por segundo",
            "bloqueadas_por_segundo": "Bloqueadas por segundo",
        }
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain != "sensor" or not entity.device_id:
                continue
            if entity.entity_id.startswith("sensor.pi_hole_extended_"):
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
            new_object_id = slugify(f"Pi-hole Extended {client_name} {metric}")
            new_entity_id = f"sensor.{new_object_id}"
            collision = 2
            while registry.async_get(new_entity_id) is not None:
                new_entity_id = f"sensor.{new_object_id}_{collision}"
                collision += 1
            registry.async_update_entity(entity.entity_id, new_entity_id=new_entity_id)

        hass.config_entries.async_update_entry(entry, version=3)
    return entry.version == 3


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a configured Pi-hole and begin tracking new DNS queries."""
    client = PiHoleClient(async_get_clientsession(hass), {**entry.data, **entry.options})
    store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry.entry_id}")
    coordinator = PiHoleQueryCoordinator(hass, entry, client, store)
    try:
        await coordinator.async_load_cursor()
        await coordinator.async_config_entry_first_refresh()
        hass.data[DOMAIN][entry.entry_id] = {"client": client, "coordinator": coordinator}
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
