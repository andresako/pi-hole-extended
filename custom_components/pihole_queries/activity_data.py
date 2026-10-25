"""Read per-client activity series from Recorder statistics.

Shared by the websocket command (activity card) and the unusual-activity
sensors so both always apply the same aggregation and detection.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .activity import (
    BASELINE_LOOKBACK_SECONDS,
    FINE_STATISTICS_SECONDS,
    DetectionParams,
    aggregate,
    params_for,
    score_series,
    statistics_period,
)
from .const import CONF_UNUSUAL_ACTIVITY, DEFAULT_UNUSUAL_ACTIVITY, DOMAIN

BASE_SECONDS = {"5minute": 300, "hour": 3600}


def is_queries_sensor(hass: HomeAssistant, entity_id: str) -> bool:
    """Return whether the entity is a per-client DNS queries counter of this integration."""
    entry = er.async_get(hass).async_get(entity_id)
    return (
        entry is not None
        and entry.platform == DOMAIN
        and entry.translation_key == "queries"
    )


def params_for_entity(hass: HomeAssistant, entity_id: str) -> DetectionParams:
    """Use the sensitivity chosen in the owning config entry's options."""
    registry_entry = er.async_get(hass).async_get(entity_id)
    config_entry_id = getattr(registry_entry, "config_entry_id", None)
    config_entry = (
        hass.config_entries.async_get_entry(config_entry_id) if config_entry_id else None
    )
    level = (
        config_entry.options.get(CONF_UNUSUAL_ACTIVITY, DEFAULT_UNUSUAL_ACTIVITY)
        if config_entry is not None
        else DEFAULT_UNUSUAL_ACTIVITY
    )
    return params_for(level)


def _fetch(
    hass: HomeAssistant,
    start: datetime,
    end: datetime,
    entity_ids: set[str],
    period: str,
) -> dict[str, list[dict[str, Any]]]:
    """Read recorder statistics; runs in the recorder executor."""
    return statistics_during_period(
        hass, start, end, entity_ids, period, None, {"change"}
    )


async def async_client_series(
    hass: HomeAssistant,
    entity_ids: set[str],
    bucket_seconds: int,
    span_seconds: int,
    params: Mapping[str, DetectionParams],
) -> dict[str, Any]:
    """Aggregate, baseline and score the given counters; one Recorder read."""
    period = statistics_period(bucket_seconds)
    base_seconds = BASE_SECONDS[period]
    now = datetime.now(UTC)
    lookback = span_seconds + BASELINE_LOOKBACK_SECONDS
    if period == "5minute":
        lookback = min(lookback, FINE_STATISTICS_SECONDS)
    start = now - timedelta(seconds=lookback)
    visible_from = (now - timedelta(seconds=span_seconds)).timestamp()
    visible_from = visible_from // bucket_seconds * bucket_seconds

    stats = await get_instance(hass).async_add_executor_job(
        _fetch, hass, start, now, entity_ids, period
    )
    series = {
        entity_id: score_series(
            aggregate(stats.get(entity_id, []), base_seconds, bucket_seconds),
            bucket_seconds,
            visible_from,
            params.get(entity_id, DetectionParams()),
        )
        for entity_id in entity_ids
    }
    return {
        "bucket_seconds": bucket_seconds,
        "span_seconds": span_seconds,
        "period": period,
        "from": visible_from,
        "to": now.timestamp(),
        "series": series,
    }
