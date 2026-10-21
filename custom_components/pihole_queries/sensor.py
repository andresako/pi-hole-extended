"""Per-client query sensors for Pi-hole Extended."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PiHoleQueryCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one query counter sensor for each discovered Pi-hole client."""
    coordinator: PiHoleQueryCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    added_clients: set[str] = set()

    @callback
    def add_new_clients() -> None:
        new_clients = set(coordinator.client_data) - added_clients
        if not new_clients:
            return
        async_add_entities(
            PiHoleClientSensor(coordinator, entry, client_key)
            for client_key in sorted(new_clients)
        )
        added_clients.update(new_clients)

    add_new_clients()
    entry.async_on_unload(coordinator.async_add_listener(add_new_clients))


class PiHoleClientSensor(CoordinatorEntity[PiHoleQueryCoordinator], SensorEntity):
    """Expose the observed query count and latest query details for a client."""

    _attr_icon = "mdi:dns"
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: PiHoleQueryCoordinator,
        entry: ConfigEntry,
        client_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._client_key = client_key
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{client_key}"

    @property
    def _client_data(self) -> dict[str, Any]:
        """Return the latest coordinator data for this client."""
        return self.coordinator.client_data.get(self._client_key, {})

    @property
    def name(self) -> str:
        """Name the entity after the Pi-hole client."""
        client_name = self._client_data.get("client_name") or "Cliente desconocido"
        client_ip = self._client_data.get("client_ip") or "Desconocida"
        if client_name == client_ip:
            return f"{client_ip} · Consultas DNS"
        return f"{client_name} ({client_ip}) · Consultas DNS"

    @property
    def native_value(self) -> int:
        """Return the number of queries observed since this entity was added."""
        try:
            return int(self._client_data.get("query_count", 0))
        except (TypeError, ValueError):
            return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose client identity, verdict totals, and a bounded query history."""
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
