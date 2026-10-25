"""Per-client query sensors for Pi-hole Extended."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from homeassistant.const import EntityCategory
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    DOMAIN,
    LEGACY_SOURCE_GENERATION,
)
from .coordinator import PiHoleQueryCoordinator
from .classification import CLASSIFIER_VERSION


SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="queries",
        name="Consultas DNS",
        translation_key="queries",
        icon="mdi:dns",
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="queries_last_minute",
        name="Consultas (último minuto)",
        translation_key="queries_last_minute",
        icon="mdi:speedometer",
        native_unit_of_measurement="consultas/min",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
)


def _classifier_descriptions(version: int) -> tuple[SensorEntityDescription, ...]:
    """Create distinct HA entities for one immutable classifier epoch."""
    suffix = f"v{version}"
    return (
        SensorEntityDescription(
            key=f"allowed_{suffix}",
            name="Consultas permitidas",
            translation_key=f"allowed_{suffix}",
            icon="mdi:check-circle-outline",
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
        SensorEntityDescription(
            key=f"blocked_{suffix}",
            name="Consultas bloqueadas",
            translation_key=f"blocked_{suffix}",
            icon="mdi:cancel",
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
        SensorEntityDescription(
            key=f"provisional_{suffix}",
            name="Consultas provisionales",
            translation_key=f"provisional_{suffix}",
            icon="mdi:help-circle-outline",
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
    )


SENSOR_DESCRIPTIONS = SENSOR_DESCRIPTIONS + _classifier_descriptions(CLASSIFIER_VERSION)

DIAGNOSTIC_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="communication_status",
        translation_key="communication_status",
        icon="mdi:lan-connect",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="last_successful_read",
        translation_key="last_successful_read",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="last_query_observed",
        translation_key="last_query_observed",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="coverage",
        translation_key="coverage",
        icon="mdi:database-search",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

_COUNT_KEYS = {
    "queries": "query_count",
    "queries_last_minute": "queries_last_minute",
}

_EPOCH_CLASS_KEYS = {"allowed", "blocked", "provisional"}


def _client_entity_unique_id(entry: ConfigEntry, client_key: str, metric_key: str) -> str:
    """Keep legacy IDs for the first source and scope replacement generations."""
    entry_data = getattr(entry, "data", {})
    generation = entry_data.get(CONF_SOURCE_GENERATION, LEGACY_SOURCE_GENERATION)
    prefix = (
        (entry.unique_id or entry.entry_id)
        if generation == LEGACY_SOURCE_GENERATION
        else f"{entry_data[CONF_SOURCE_ID]}_{generation}"
    )
    suffix = "" if metric_key == "queries" else f"_{metric_key}"
    return f"{prefix}_{client_key}{suffix}"


def _client_device_identifier(entry: ConfigEntry, client_key: str) -> tuple[str, str]:
    """Return a per-entry legacy identifier or source-generation identifier."""
    entry_data = getattr(entry, "data", {})
    generation = entry_data.get(CONF_SOURCE_GENERATION, LEGACY_SOURCE_GENERATION)
    identifier = (
        f"{entry.entry_id}_{client_key}"
        if generation == LEGACY_SOURCE_GENERATION
        else f"{entry_data[CONF_SOURCE_ID]}_{generation}_{client_key}"
    )
    return DOMAIN, identifier

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
    async_add_entities(
        PiHoleDiagnosticSensor(coordinator, entry, description)
        for description in DIAGNOSTIC_DESCRIPTIONS
    )
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
        self._client_key = client_key
        self._entry = entry
        # The device carries the client name; the entity label only describes
        # the metric, independently of its technical registry ID.
        self.entity_description = description
        # Keep the initial generation's registry identity; replacement generations are scoped.
        self._attr_unique_id = _client_entity_unique_id(entry, client_key, description.key)

    @property
    def _client_data(self) -> dict[str, Any]:
        """Return the latest coordinator data for this client."""
        return self.coordinator.client_data.get(self._client_key, {})

    @property
    def device_info(self) -> DeviceInfo:
        """Group the client's query sensors under one Home Assistant device."""
        data = self._client_data
        client_name = str(data.get("client_name") or "Cliente desconocido")
        client_ip = str(data.get("client_ip") or "")
        if client_name == "Cliente desconocido":
            language = self.coordinator.hass.config.language.split("-")[0]
            client_name = "Unknown client" if language == "en" else "Cliente desconocido"
        name = (
            f"{client_name} ({client_ip})"
            if client_ip and client_ip != "Desconocida"
            else client_name
        )
        return DeviceInfo(
            identifiers={_client_device_identifier(self._entry, self._client_key)},
            translation_key="client_device",
            translation_placeholders={"client_name": name},
            manufacturer="Pi-hole",
        )

    @property
    def native_value(self) -> int | float | None:
        """Return this entity's accumulated count."""
        data = self._client_data
        metric = self.entity_description.key
        versioned = metric.rsplit("_v", maxsplit=1)
        if len(versioned) == 2 and versioned[1].isdigit():
            base, version_text = versioned
            version = version_text
            if base in _EPOCH_CLASS_KEYS:
                counts = data.get("classifier_counts", {})
                if not isinstance(counts, dict):
                    return 0
                epoch = counts.get(version, {})
                if not isinstance(epoch, dict):
                    return 0
                try:
                    return int(epoch.get(base, 0))
                except (TypeError, ValueError):
                    return 0
        if metric == "queries_last_minute":
            value = data.get(_COUNT_KEYS[metric])
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            return value
        if metric in _COUNT_KEYS:
            try:
                return int(data.get(_COUNT_KEYS[metric], 0))
            except (TypeError, ValueError):
                return 0
        return 0

    @property
    def available(self) -> bool:
        """Require a fresh, complete source-time window for this rolling count."""
        if not super().available:
            return False
        if self.entity_description.key != "queries_last_minute":
            return True
        if (
            not getattr(self.coordinator, "query_time_window_ready", False)
            or getattr(self.coordinator, "coverage", "unknown") != "complete"
        ):
            return False
        last_success = getattr(self.coordinator, "last_successful_read", None)
        if not isinstance(last_success, datetime):
            return False
        last_success = last_success.astimezone(UTC)
        now = dt_util.utcnow()
        if last_success > now:
            return False
        interval = self.coordinator.update_interval.total_seconds()
        return (now - last_success).total_seconds() <= 2 * interval

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the observation window's time source and sample freshness."""
        if self.entity_description.key == "queries_last_minute":
            return {
                "window_seconds": 60,
                "timestamp_source": "Pi-hole query.time, with HA observation fallback",
                "last_successful_read": getattr(
                    self.coordinator, "last_successful_read", None
                ),
            }
        if self.entity_description.key != "queries":
            return None
        data = self._client_data
        version = str(data.get("classifier_version", CLASSIFIER_VERSION))
        counts = data.get("classifier_counts", {})
        active_counts = counts.get(version, {}) if isinstance(counts, dict) else {}
        if not isinstance(active_counts, dict):
            active_counts = {}
        rates = data.get("classifier_rates", {})
        active_rates = rates.get(version, {}) if isinstance(rates, dict) else {}
        if not isinstance(active_rates, dict):
            active_rates = {}
        try:
            allowed = int(active_counts.get("allowed", 0))
            blocked = int(active_counts.get("blocked", 0))
            provisional = int(active_counts.get("provisional", 0))
        except (TypeError, ValueError):
            allowed = blocked = provisional = 0
        classified = allowed + blocked
        attributes = {
            "client_name": data.get("client_name"),
            "client_ip": data.get("client_ip"),
            "queries_observed": self.native_value,
            "legacy_allowed_count": data.get("allowed_count", 0),
            "legacy_blocked_count": data.get("blocked_count", 0),
            "active_classifier_version": data.get("classifier_version", CLASSIFIER_VERSION),
            "classifier_counts": data.get("classifier_counts", {}),
            "queries_observed_last_minute": data.get("queries_observed_last_minute", 0),
            "classifier_rates_last_minute": active_rates,
            "classified_count": classified,
            "blocked_percentage": (100 * blocked / classified) if classified else None,
            "provisional_count": provisional,
        }
        return attributes


class PiHoleDiagnosticSensor(CoordinatorEntity[PiHoleQueryCoordinator], SensorEntity):
    """Expose communication, sample freshness and coverage per integration entry."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: PiHoleQueryCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def available(self) -> bool:
        """Keep diagnostic values visible while the API is failing or stale."""
        return True

    @property
    def device_info(self) -> DeviceInfo:
        """Group diagnostics under one device representing this Pi-hole entry."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_diagnostics")},
            translation_key="pihole_instance",
            translation_placeholders={"entry_title": self._entry.title},
            manufacturer="Pi-hole",
        )

    @property
    def native_value(self) -> str | datetime | None:
        """Return a conservative state backed by coordinator evidence."""
        key = self.entity_description.key
        if key == "communication_status":
            last_success = getattr(self.coordinator, "last_successful_read", None)
            last_api_attempt = getattr(self.coordinator, "last_api_attempt_successful", None)
            if last_api_attempt is False:
                return "error"
            if last_success is None or last_api_attempt is None:
                return "unknown"
            now = dt_util.utcnow()
            last_success = last_success.astimezone(UTC)
            if last_success > now:
                return "unknown"
            interval = self.coordinator.update_interval.total_seconds()
            age = (now - last_success).total_seconds()
            return "stale" if age > 2 * interval else "connected"
        if key == "last_successful_read":
            return getattr(self.coordinator, "last_successful_read", None)
        if key == "last_query_observed":
            timestamp = getattr(self.coordinator, "last_query_time", None)
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                return None
            if not math.isfinite(timestamp):
                return None
            try:
                sampled_at = datetime.fromtimestamp(timestamp, tz=UTC)
            except (OSError, OverflowError, ValueError):
                return None
            # A future source timestamp cannot support a meaningful sample age.
            if sampled_at > dt_util.utcnow():
                return None
            return sampled_at
        if key == "coverage":
            return getattr(self.coordinator, "coverage", "unknown")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Explain the evidence and limits behind each diagnostic value."""
        key = self.entity_description.key
        if key == "communication_status":
            last_success = getattr(self.coordinator, "last_successful_read", None)
            interval = self.coordinator.update_interval.total_seconds()
            return {
                "last_successful_read": last_success,
                "freshness_threshold_seconds": 2 * interval,
                "freshness": self.native_value,
                "coordinator_update_success": self.coordinator.last_update_success,
                "persistence": "unverified",
            }
        if key == "last_query_observed":
            sampled_at = self.native_value
            sample_age = (
                max(0, (dt_util.utcnow() - sampled_at).total_seconds())
                if isinstance(sampled_at, datetime)
                else None
            )
            return {
                "sample_age_seconds": sample_age,
                "timestamp_source": "Pi-hole query.time",
                "sample_age_status": "known" if sample_age is not None else "unknown",
            }
        if key == "coverage":
            return {
                "coverage_reason": getattr(self.coordinator, "coverage_reason", None),
                "activity_persistence": "unverified",
                "recorder_retention": "unknown",
            }
        return None
