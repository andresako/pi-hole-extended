"""Per-client query sensors for Pi-hole Extended."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PiHoleQueryCoordinator


SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="queries",
        name="Consultas DNS",
        icon="mdi:dns",
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="allowed",
        name="Permitidas",
        icon="mdi:check-circle-outline",
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="blocked",
        name="Bloqueadas",
        icon="mdi:cancel",
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="query_rate",
        name="Consultas por segundo",
        icon="mdi:speedometer",
        native_unit_of_measurement="consultas/s",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="allowed_rate",
        name="Permitidas por segundo",
        icon="mdi:check-circle-outline",
        native_unit_of_measurement="consultas/s",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="blocked_rate",
        name="Bloqueadas por segundo",
        icon="mdi:cancel",
        native_unit_of_measurement="consultas/s",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
)

_COUNT_KEYS = {
    "queries": "query_count",
    "allowed": "allowed_count",
    "blocked": "blocked_count",
}

_RATE_KEYS = {"query_rate", "allowed_rate", "blocked_rate"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a device with simple query sensors for each discovered client."""
    coordinator: PiHoleQueryCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    added_clients: set[str] = set()

    @callback
    def add_new_clients() -> None:
        new_clients = set(coordinator.client_data) - added_clients
        if not new_clients:
            return
        async_add_entities(
            PiHoleClientSensor(coordinator, entry, client_key, description)
            for client_key in sorted(new_clients)
            for description in SENSOR_DESCRIPTIONS
        )
        added_clients.update(new_clients)

    add_new_clients()
    entry.async_on_unload(coordinator.async_add_listener(add_new_clients))


class PiHoleClientSensor(CoordinatorEntity[PiHoleQueryCoordinator], SensorEntity):
    """Expose one simple query metric on a per-client Home Assistant device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: PiHoleQueryCoordinator,
        entry: ConfigEntry,
        client_key: str,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._client_key = client_key
        self._entry = entry
        # Keep the original counter unique_id so updating does not create a duplicate entity.
        suffix = "" if description.key == "queries" else f"_{description.key}"
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{client_key}{suffix}"

    @property
    def _client_data(self) -> dict[str, Any]:
        """Return the latest coordinator data for this client."""
        return self.coordinator.client_data.get(self._client_key, {})

    @property
    def device_info(self) -> DeviceInfo:
        """Group the client's query sensors under one Home Assistant device."""
        data = self._client_data
        client_name = str(data.get("client_name") or "Cliente desconocido")
        client_ip = str(data.get("client_ip") or "Desconocida")
        name = client_name if client_name == client_ip else f"{client_name} ({client_ip})"
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_{self._client_key}")},
            name=name,
            manufacturer="Pi-hole",
            model="Cliente DNS",
        )

    @property
    def native_value(self) -> int | float:
        """Return this entity's accumulated count or latest query rate."""
        data = self._client_data
        metric = self.entity_description.key
        if metric in _COUNT_KEYS:
            try:
                return int(data.get(_COUNT_KEYS[metric], 0))
            except (TypeError, ValueError):
                return 0
        if metric in _RATE_KEYS:
            try:
                return float(data.get(metric, 0.0))
            except (TypeError, ValueError):
                return 0.0
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Keep detailed and recent query data on the total counter entity."""
        if self.entity_description.key != "queries":
            return None
        data = self._client_data
        return {
            "client_name": data.get("client_name"),
            "client_ip": data.get("client_ip"),
            "queries_observed": self.native_value,
            "allowed_queries_observed": data.get("allowed_count", 0),
            "blocked_queries_observed": data.get("blocked_count", 0),
            "last_query": data.get("last_query"),
            "recent_queries": data.get("recent_queries", []),
        }
