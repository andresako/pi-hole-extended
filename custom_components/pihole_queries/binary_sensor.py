"""Per-client unusual-activity binary sensors."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .activity_monitor import UnusualActivityMonitor
from .const import (
    CONF_UNUSUAL_ACTIVITY,
    DEFAULT_UNUSUAL_ACTIVITY,
    DOMAIN,
    UNUSUAL_OFF,
)
from .coordinator import PiHoleQueryCoordinator
from .entity_ids import canonical_entity_id, client_entity_unique_id
from .sensor import client_device_info

UNUSUAL_ACTIVITY_DESCRIPTION = BinarySensorEntityDescription(
    key="unusual_activity",
    translation_key="unusual_activity",
    device_class=BinarySensorDeviceClass.PROBLEM,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one unusual-activity sensor per client unless the option is off."""
    if entry.options.get(CONF_UNUSUAL_ACTIVITY, DEFAULT_UNUSUAL_ACTIVITY) == UNUSUAL_OFF:
        return
    coordinator: PiHoleQueryCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    monitor = UnusualActivityMonitor(hass, entry, coordinator)
    added: set[str] = set()

    @callback
    def add_new_clients() -> None:
        new_clients = set(coordinator.client_data) - added
        if not new_clients:
            return
        async_add_entities(
            PiHoleUnusualActivitySensor(coordinator, monitor, entry, client_key)
            for client_key in sorted(new_clients)
        )
        added.update(new_clients)

    add_new_clients()
    entry.async_on_unload(coordinator.async_add_listener(add_new_clients))
    monitor.async_start()


class PiHoleUnusualActivitySensor(BinarySensorEntity):
    """On while a client stays well above its own usual query rate."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description = UNUSUAL_ACTIVITY_DESCRIPTION

    def __init__(
        self,
        coordinator: PiHoleQueryCoordinator,
        monitor: UnusualActivityMonitor,
        entry: ConfigEntry,
        client_key: str,
    ) -> None:
        self._coordinator = coordinator
        self._monitor = monitor
        self._entry = entry
        self._client_key = client_key
        self._attr_unique_id = client_entity_unique_id(entry, client_key, "unusual_activity")
        # Ask for the phe_ ID up front: renaming after creation can race the platform add.
        suggested = canonical_entity_id(entry, "binary_sensor", self._attr_unique_id)
        if suggested:
            self.entity_id = suggested

    @property
    def device_info(self) -> DeviceInfo:
        """Share the client's device with its query sensors."""
        return client_device_info(self._coordinator, self._entry, self._client_key)

    @property
    def _result(self) -> dict[str, Any]:
        return self._monitor.results.get(self._client_key, {})

    @property
    def is_on(self) -> bool | None:
        """Unknown until the client has enough history for a baseline."""
        return self._result.get("unusual")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        result = self._result
        if not result.get("ready"):
            return None
        since = result.get("since")
        return {
            "current_rate": round(result["rate"], 2),
            "usual_rate": round(result["usual"], 2),
            "threshold": round(result["threshold"], 2),
            "times_usual": round(result["multiple"], 1) if result["multiple"] else None,
            "since": datetime.fromtimestamp(since, UTC) if since else None,
            "evaluated_at": self._monitor.evaluated_at,
        }

    async def async_added_to_hass(self) -> None:
        """Refresh the state after each evaluation."""
        self.async_on_remove(self._monitor.async_add_listener(self.async_write_ha_state))
