"""Websocket API feeding the activity card with per-client series."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv

from .activity import BUCKET_SECONDS, SPAN_SECONDS, validate_selection
from .activity_data import async_client_series, is_queries_sensor, params_for_entity
from .const import DOMAIN

WS_CLIENT_ACTIVITY = f"{DOMAIN}/client_activity"
MAX_ENTITIES = 12


class ActivityRequestError(Exception):
    """Request rejected with a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the websocket commands."""
    websocket_api.async_register_command(hass, ws_client_activity)


async def async_client_activity(hass: HomeAssistant, msg: dict[str, Any]) -> dict[str, Any]:
    """Build aggregated series with baseline and unusual-activity runs."""
    problem = validate_selection(msg["bucket"], msg["span"])
    if problem:
        raise ActivityRequestError(problem, problem)
    entity_ids = set(msg["entity_ids"])
    invalid = sorted(e for e in entity_ids if not is_queries_sensor(hass, e))
    if invalid:
        raise ActivityRequestError("invalid_entity", ", ".join(invalid))
    return await async_client_series(
        hass,
        entity_ids,
        BUCKET_SECONDS[msg["bucket"]],
        SPAN_SECONDS[msg["span"]],
        {entity_id: params_for_entity(hass, entity_id) for entity_id in entity_ids},
    )


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
