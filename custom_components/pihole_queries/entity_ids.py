"""Stable unique IDs, device identifiers and `phe_` entity IDs for client entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import (
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    DOMAIN,
    FIRST_SOURCE_GENERATION,
)


def entity_unique_prefix(entry: ConfigEntry) -> str:
    """Scope unique IDs and device identifiers to one source generation."""
    return f"{entry.data[CONF_SOURCE_ID]}_{entry.data[CONF_SOURCE_GENERATION]}"


def client_entity_unique_id(entry: ConfigEntry, client_key: str, metric_key: str) -> str:
    """Return the unique ID of one client metric."""
    suffix = "" if metric_key == "queries" else f"_{metric_key}"
    return f"{entity_unique_prefix(entry)}_{client_key}{suffix}"


def client_device_identifier(entry: ConfigEntry, client_key: str) -> tuple[str, str]:
    """Return the device identifier of one client."""
    return DOMAIN, f"{entity_unique_prefix(entry)}_{client_key}"


def available_entity_id(registry: er.EntityRegistry, entity_id: str) -> str:
    """Return an unused entity ID, adding a numeric suffix if necessary."""
    base_entity_id = entity_id
    collision = 2
    while registry.async_get(entity_id) is not None:
        entity_id = f"{base_entity_id}_{collision}"
        collision += 1
    return entity_id


def canonical_entity_id(entry: ConfigEntry, domain: str, unique_id: str) -> str | None:
    """Return the `phe_` entity ID for a client entity, or None if not one of ours."""
    if domain not in ("sensor", "binary_sensor"):
        return None
    metric_suffixes = {
        "_queries_last_minute": "queries_last_minute",
        "_unusual_activity": "unusual_activity",
        "_allowed": "allowed",
        "_blocked": "blocked",
        "_provisional": "provisional",
        "": "dns_queries",
    }
    suffix = next(suffix for suffix in metric_suffixes if unique_id.endswith(suffix))
    unique_prefix = f"{entity_unique_prefix(entry)}_"
    if not unique_id.startswith(unique_prefix):
        return None
    client_key = unique_id.removeprefix(unique_prefix).removesuffix(suffix)
    generation = entry.data[CONF_SOURCE_GENERATION]
    entity_client = (
        client_key if generation == FIRST_SOURCE_GENERATION else f"{generation}_{client_key}"
    )
    return f"{domain}.phe_{slugify(entity_client)}_{metric_suffixes[suffix]}"


def diagnostic_entity_id(entry: ConfigEntry, key: str) -> str:
    """Return the entity ID of a diagnostic sensor, independent of the HA language."""
    metric = "ingestion_coverage" if key == "coverage" else key
    return f"sensor.{slugify(entry.title)}_diagnostics_{metric}"
