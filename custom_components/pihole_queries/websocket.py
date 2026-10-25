"""Websocket API feeding the activity card with per-client series."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .activity import (
    BASELINE_LOOKBACK_SECONDS,
    BUCKET_SECONDS,
    FINE_STATISTICS_SECONDS,
    SPAN_SECONDS,
    DetectionParams,
    aggregate,
    score_series,
    statistics_period,
    validate_selection,
)
from .const import DOMAIN

WS_CLIENT_ACTIVITY = f"{DOMAIN}/client_activity"
MAX_ENTITIES = 12
BASE_SECONDS = {"5minute": 300, "hour": 3600}


class ActivityRequestError(Exception):
    """Request rejected with a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_queries_sensor(hass: HomeAssistant, entity_id: str) -> bool:
    entry = er.async_get(hass).async_get(entity_id)
    return (
        entry is not None
        and entry.platform == DOMAIN
        and entry.translation_key == "queries"
    )


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


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the websocket commands."""
    websocket_api.async_register_command(hass, ws_client_activity)


async def async_client_activity(hass: HomeAssistant, msg: dict[str, Any]) -> dict[str, Any]:
    """Build aggregated series with baseline and unusual-activity runs."""
    problem = validate_selection(msg["bucket"], msg["span"])
    if problem:
        raise ActivityRequestError(problem, "Unsupported bucket/span")
    entity_ids = set(msg["entity_ids"])
    invalid = sorted(e for e in entity_ids if not _is_queries_sensor(hass, e))
    if invalid:
        raise ActivityRequestError("invalid_entity", ", ".join(invalid))

    bucket_seconds = BUCKET_SECONDS[msg["bucket"]]
    span_seconds = SPAN_SECONDS[msg["span"]]
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

    params = DetectionParams()
    series = {
        entity_id: score_series(
            aggregate(stats.get(entity_id, []), base_seconds, bucket_seconds),
            bucket_seconds,
            visible_from,
            params,
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_CLIENT_ACTIVITY,
        vol.Required("entity_ids"): vol.All(
            [cv.entity_id], vol.Length(min=1, max=MAX_ENTITIES)
        ),
        vol.Required("bucket"): vol.In(list(BUCKET_SECONDS)),
        vol.Required("span"): vol.In(list(SPAN_SECONDS)),
    }
)
@websocket_api.async_response
async def ws_client_activity(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return per-client activity for the card."""
    try:
        result = await async_client_activity(hass, msg)
    except ActivityRequestError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    connection.send_result(msg["id"], result)
