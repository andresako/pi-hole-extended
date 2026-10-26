"""Stable unique IDs, device identifiers and `phe_` entity IDs for client entities."""

from __future__ import annotations

import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import (
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    DOMAIN,
    LEGACY_SOURCE_GENERATION,
)


def entity_unique_prefix(entry: ConfigEntry) -> str:
    """Keep first-generation unique IDs and scope replacement generations."""
    generation = entry.data.get(CONF_SOURCE_GENERATION, LEGACY_SOURCE_GENERATION)
    if generation == LEGACY_SOURCE_GENERATION:
        return entry.unique_id or entry.entry_id
    return f"{entry.data[CONF_SOURCE_ID]}_{generation}"


def client_entity_unique_id(entry: ConfigEntry, client_key: str, metric_key: str) -> str:
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


def client_device_identifier(entry: ConfigEntry, client_key: str) -> tuple[str, str]:
    """Return a per-entry legacy identifier or source-generation identifier."""
    entry_data = getattr(entry, "data", {})
    generation = entry_data.get(CONF_SOURCE_GENERATION, LEGACY_SOURCE_GENERATION)
    identifier = (
        f"{entry.entry_id}_{client_key}"
        if generation == LEGACY_SOURCE_GENERATION
        else f"{entry_data[CONF_SOURCE_ID]}_{generation}_{client_key}"
    )
    return DOMAIN, identifier


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
    versioned_classes = {
        "allowed": "permitidas",
        "blocked": "bloqueadas",
        "provisional": "provisionales",
    }
    versioned_match = re.search(
        r"_(allowed|blocked|provisional)_v(\d+)$", unique_id
    )
    metric_suffixes = {
        "_allowed": "permitidas",
        "_blocked": "bloqueadas",
        "_queries_last_minute": "consultas_en_el_ultimo_minuto",
        "_unusual_activity": "actividad_inusual",
        "_allowed_last_minute": "permitidas_en_el_ultimo_minuto",
        "_blocked_last_minute": "bloqueadas_en_el_ultimo_minuto",
        "": "consultas_dns",
    }
    if versioned_match:
        classifier, version = versioned_match.groups()
        suffix = versioned_match.group(0)
        metric_name = f"{versioned_classes[classifier]}_v{version}"
    else:
        suffix = next(
            (suffix for suffix in metric_suffixes if unique_id.endswith(suffix)),
            None,
        )
        metric_name = metric_suffixes.get(suffix) if suffix is not None else None
    unique_prefix = f"{entity_unique_prefix(entry)}_"
    if metric_name is None or not unique_id.startswith(unique_prefix):
        return None
    client_key = unique_id.removeprefix(unique_prefix).removesuffix(suffix)
    entity_client = (
        client_key
        if entry.data.get(CONF_SOURCE_GENERATION, LEGACY_SOURCE_GENERATION)
        == LEGACY_SOURCE_GENERATION
        else f"{entry.data[CONF_SOURCE_GENERATION]}_{client_key}"
    )
    return f"{domain}.phe_{slugify(entity_client)}_{metric_name}"
