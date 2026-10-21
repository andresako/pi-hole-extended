"""Pi-hole v6 DNS query history integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import PiHoleClient
from .const import DOMAIN, PLATFORMS, STORAGE_KEY_PREFIX, STORAGE_VERSION
from .coordinator import PiHoleQueryCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration domain."""
    hass.data.setdefault(DOMAIN, {})
    return True


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
