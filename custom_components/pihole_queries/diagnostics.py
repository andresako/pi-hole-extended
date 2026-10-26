"""Downloadable diagnostics with credentials and client identities redacted."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    DOMAIN,
)

TO_REDACT = {CONF_HOST, CONF_PASSWORD, CONF_SOURCE_ID, CONF_SOURCE_GENERATION}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return ingestion state and per-client totals, without names or IPs."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    clients = [
        {
            "query_count": summary.get("query_count"),
            "queries_last_minute": summary.get("queries_last_minute"),
            "classifier_version": summary.get("classifier_version"),
        }
        for summary in coordinator.client_data.values()
    ]
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "cursor": coordinator.cursor,
            "coverage": str(coordinator.coverage),
            "coverage_reason": (
                str(coordinator.coverage_reason) if coordinator.coverage_reason else None
            ),
            "awaiting_generation": coordinator.awaiting_generation,
            "recovery_cursor": coordinator.recovery_cursor,
            "last_query_time": coordinator.last_query_time,
            "last_update_success": coordinator.last_update_success,
            "last_successful_read": (
                coordinator.last_successful_read.isoformat()
                if coordinator.last_successful_read
                else None
            ),
            "retention_mode": coordinator.retention_mode,
            "activity_mode": coordinator.activity_mode,
            "update_interval_seconds": coordinator.update_interval.total_seconds(),
        },
        "client_count": len(clients),
        "clients": clients,
    }
