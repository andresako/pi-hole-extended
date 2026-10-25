"""Periodic unusual-activity evaluation for every client of one Pi-hole entry."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .activity import BUCKET_SECONDS, params_for
from .activity_data import async_client_series
from .const import CONF_UNUSUAL_ACTIVITY, DEFAULT_UNUSUAL_ACTIVITY, DOMAIN
from .coordinator import PiHoleQueryCoordinator
from .sensor import _client_entity_unique_id

_LOGGER = logging.getLogger(__name__)

EVALUATION_INTERVAL = timedelta(minutes=5)
FIRST_EVALUATION_DELAY = 30
EVALUATION_BUCKET = BUCKET_SECONDS["5m"]
EVALUATION_SPAN = 6 * 3600
READ_CHUNK = 12


def summarize(series: dict[str, Any]) -> dict[str, Any]:
    """Reduce a scored series to what a binary sensor exposes."""
    latest = series.get("latest")
    if not series.get("has_baseline") or latest is None:
        return {"ready": False, "unusual": None}
    since = None
    if latest["unusual"] and series["runs"]:
        since = series["runs"][-1]["start"]
    return {
        "ready": True,
        "unusual": bool(latest["unusual"]),
        "rate": latest["rate"],
        "usual": latest["base"],
        "threshold": latest["threshold"],
        "multiple": latest["rate"] / latest["base"] if latest["base"] else None,
        "since": since,
    }


class UnusualActivityMonitor:
    """Evaluate each client's recent rate against its own history."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: PiHoleQueryCoordinator,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._listeners: list[Callable[[], None]] = []
        self.results: dict[str, dict[str, Any]] = {}
        self.evaluated_at: datetime | None = None

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call `listener` after every evaluation; returns the unsubscribe function."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            self._listeners.remove(listener)

        return remove

    @callback
    def async_start(self) -> None:
        """Schedule the first and the recurring evaluations."""
        self._entry.async_on_unload(
            async_call_later(self._hass, FIRST_EVALUATION_DELAY, self._async_evaluate)
        )
        self._entry.async_on_unload(
            async_track_time_interval(
                self._hass, self._async_evaluate, EVALUATION_INTERVAL
            )
        )

    def _counter_entity_ids(self) -> dict[str, str]:
        """Map client keys to the entity ID of their DNS queries counter."""
        registry = er.async_get(self._hass)
        mapping = {}
        for client_key in self._coordinator.client_data:
            entity_id = registry.async_get_entity_id(
                "sensor",
                DOMAIN,
                _client_entity_unique_id(self._entry, client_key, "queries"),
            )
            if entity_id:
                mapping[client_key] = entity_id
        return mapping

    async def _async_evaluate(self, _now: datetime | None = None) -> None:
        mapping = self._counter_entity_ids()
        if not mapping:
            return
        level = self._entry.options.get(CONF_UNUSUAL_ACTIVITY, DEFAULT_UNUSUAL_ACTIVITY)
        params = params_for(level)
        by_entity = {entity_id: client for client, entity_id in mapping.items()}
        entity_ids = sorted(by_entity)
        results: dict[str, dict[str, Any]] = {}
        try:
            for start in range(0, len(entity_ids), READ_CHUNK):
                chunk = set(entity_ids[start : start + READ_CHUNK])
                data = await async_client_series(
                    self._hass,
                    chunk,
                    EVALUATION_BUCKET,
                    EVALUATION_SPAN,
                    {entity_id: params for entity_id in chunk},
                )
                for entity_id, series in data["series"].items():
                    results[by_entity[entity_id]] = summarize(series)
        except Exception:  # noqa: BLE001 - keep the previous result on any Recorder failure
            _LOGGER.debug("Unusual-activity evaluation failed", exc_info=True)
            return
        self.results = results
        self.evaluated_at = datetime.now().astimezone()
        for listener in list(self._listeners):
            listener()
