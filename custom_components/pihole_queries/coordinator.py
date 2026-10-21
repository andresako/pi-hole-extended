"""Poll Pi-hole queries, record Activity entries, and aggregate per-client data."""

from __future__ import annotations

import logging
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import PiHoleAuthenticationError, PiHoleClient, PiHoleConnectionError
from .const import (
    CLIENT_QUERY_HISTORY,
    CONF_SCAN_INTERVAL,
    DOMAIN,
    QUERY_BATCH_SIZE,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

BLOCKED_MARKERS = ("GRAVITY", "REGEX", "DENYLIST", "BLOCKED", "BLOCKLIST")


def _query_id(query: dict[str, Any]) -> int | None:
    """Return a valid numeric query ID, if present."""
    try:
        return int(query["id"])
    except (KeyError, TypeError, ValueError):
        return None


def _blocked(status: Any) -> bool:
    """Interpret the Pi-hole query status string without losing its raw value."""
    normalized = str(status or "").upper()
    return any(marker in normalized for marker in BLOCKED_MARKERS)


def _query_description(query: dict[str, Any]) -> tuple[str, str]:
    """Build a readable Activity name and message from available query fields."""
    client = query.get("client") or {}
    if not isinstance(client, dict):
        client = {}
    reply = query.get("reply") or {}
    if not isinstance(reply, dict):
        reply = {}
    ede = query.get("ede") or {}
    if not isinstance(ede, dict):
        ede = {}
    client_ip = str(client.get("ip") or "IP desconocida")
    client_name = str(client.get("name") or client_ip)
    domain = str(query.get("domain") or "dominio desconocido")
    status = str(query.get("status") or "desconocido")
    verdict = "bloqueada" if _blocked(status) else "permitida"
    name = f"Pi-hole · {client_name} ({client_ip})"
    details = [f"Consulta {verdict}: {domain}", f"Estado: {status}"]
    for label, value in (
        ("Tipo", query.get("type")),
        ("Respuesta", reply.get("type")),
        ("Tiempo de respuesta (s)", reply.get("time")),
        ("CNAME bloqueado", query.get("cname")),
        ("ID de lista/regla", query.get("list_id")),
        ("DNSSEC", query.get("dnssec")),
        ("Servidor ascendente", query.get("upstream")),
        ("Motivo extendido", ede.get("text")),
    ):
        if value not in (None, ""):
            details.append(f"{label}: {value}")
    return name, " · ".join(details)


def _client_key(query: dict[str, Any]) -> tuple[str, str, str]:
    """Return a stable per-client key, display name, and IP address."""
    client = query.get("client") or {}
    if not isinstance(client, dict):
        client = {}
    client_ip = str(client.get("ip") or "").strip()
    client_name = str(client.get("name") or "").strip()
    key = client_ip or client_name or "unknown"
    return key, client_name or client_ip or "Cliente desconocido", client_ip or "Desconocida"


def _query_summary(query: dict[str, Any], query_id: int) -> dict[str, Any]:
    """Keep the useful fields for a client's entity attributes."""
    timestamp = query.get("time")
    try:
        event_time = datetime.fromtimestamp(float(timestamp), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        event_time = datetime.now(UTC)

    reply = query.get("reply") or {}
    if not isinstance(reply, dict):
        reply = {}
    ede = query.get("ede") or {}
    if not isinstance(ede, dict):
        ede = {}

    return {
        "id": query_id,
        "time": dt_util.as_local(event_time).isoformat(sep=" ", timespec="seconds"),
        "domain": str(query.get("domain") or "Desconocido"),
        "result": "bloqueada" if _blocked(query.get("status")) else "permitida",
        "status": str(query.get("status") or "desconocido"),
        "type": query.get("type"),
        "reply": reply.get("type"),
        "reply_time": reply.get("time"),
        "cname": query.get("cname"),
        "list_id": query.get("list_id"),
        "dnssec": query.get("dnssec"),
        "upstream": query.get("upstream"),
        "ede": ede.get("text"),
    }


class PiHoleQueryCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Poll query IDs and maintain bounded, persistent summaries for each client."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: Any,
        client: PiHoleClient,
        store: Store[dict[str, Any]],
    ) -> None:
        self.client = client
        self.store = store
        self.cursor: int | None = None
        self.client_data: dict[str, dict[str, Any]] = {}
        self._bootstrapped = False
        self._last_rate_sample_at: float | None = None
        self._last_rate_counts: dict[str, tuple[int, int, int]] = {}
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, entry.data[CONF_SCAN_INTERVAL])
            ),
        )

    async def async_load_cursor(self) -> None:
        """Restore the query cursor and client summaries for this config entry."""
        saved = await self.store.async_load()
        if saved is None:
            return
        try:
            self.cursor = int(saved["cursor"])
            self._bootstrapped = True
        except (KeyError, TypeError, ValueError):
            _LOGGER.warning("Se ignoró un cursor local de Pi-hole no válido")

        clients = saved.get("clients", {})
        if isinstance(clients, dict):
            self.client_data = {
                str(key): value
                for key, value in clients.items()
                if isinstance(value, dict)
            }

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch new queries and return a snapshot of per-client summaries."""
        try:
            pages: list[dict[str, Any]] = [
                await self.client.async_get_queries(length=QUERY_BATCH_SIZE)
            ]

            if not self._bootstrapped:
                # Seed client entities from the current page, without logging or
                # counting old queries as newly observed.
                self._seed_clients(pages[0].get("queries", []))
                ids = [
                    value
                    for row in pages[0].get("queries", [])
                    if isinstance(row, dict) and (value := _query_id(row)) is not None
                ]
                self.cursor = max(ids, default=0)
                self._bootstrapped = True
                self._update_query_rates(time.monotonic())
                await self._save_cursor()
                _LOGGER.info("Pi-hole query tracking initialized at query ID %s", self.cursor)
                return deepcopy(self.client_data)

            # Also discover clients on upgrade from older releases that only
            # stored a cursor. Seeding does not create Activity entries.
            if not self.client_data:
                self._seed_clients(pages[0].get("queries", []))

            # Pi-hole returns newest records first. Follow cursors until the
            # previously stored high-water mark is reached.
            seen_cursors: set[int] = set()
            while True:
                rows = pages[-1].get("queries", [])
                ids = [
                    value
                    for row in rows
                    if isinstance(row, dict) and (value := _query_id(row)) is not None
                ]
                if not rows or (self.cursor is not None and ids and min(ids) <= self.cursor):
                    break
                next_cursor = pages[-1].get("cursor")
                if next_cursor is None:
                    break
                try:
                    next_cursor = int(next_cursor)
                except (TypeError, ValueError) as err:
                    raise UpdateFailed("La API de Pi-hole devolvió un cursor no válido") from err
                if next_cursor in seen_cursors:
                    raise UpdateFailed("La paginación de /api/queries no avanzó")
                seen_cursors.add(next_cursor)
                pages.append(
                    await self.client.async_get_queries(
                        length=QUERY_BATCH_SIZE,
                        cursor=next_cursor,
                    )
                )

            rows = [
                row
                for result in pages
                for row in result.get("queries", [])
                if isinstance(row, dict)
            ]
            identified = [
                (query_id, row)
                for row in rows
                if (query_id := _query_id(row)) is not None
            ]
            if not identified:
                self._update_query_rates(time.monotonic())
                await self._save_cursor()
                return deepcopy(self.client_data)

            newest_id = max(query_id for query_id, _ in identified)
            if self.cursor is not None and newest_id < self.cursor:
                # Pi-hole's query database was recreated or reset. Start a new
                # cursor baseline; old and new database IDs cannot be matched.
                _LOGGER.warning(
                    "El identificador de consultas de Pi-hole se reinició; se establece un cursor nuevo"
                )
                for summary in self.client_data.values():
                    summary["last_query_id"] = 0
                    summary.pop("last_query", None)
                    summary["recent_queries"] = []
                self._seed_clients(pages[0].get("queries", []))
                self.cursor = newest_id
                self._update_query_rates(time.monotonic())
                await self._save_cursor()
                return deepcopy(self.client_data)

            new_rows = sorted(
                (
                    (query_id, row)
                    for query_id, row in identified
                    if query_id > (self.cursor or 0)
                ),
                key=lambda pair: pair[0],
            )
            try:
                for query_id, row in new_rows:
                    await self._log_query(row)
                    self._update_client(row, query_id, count_as_new=True)
                    # If a later Activity write fails, persist the successfully
                    # logged prefix so it is not counted or logged again.
                    self.cursor = query_id
            except HomeAssistantError:
                await self._save_cursor()
                raise

            self.cursor = max(self.cursor or 0, newest_id)
            self._update_query_rates(time.monotonic())
            await self._save_cursor()
            return deepcopy(self.client_data)
        except PiHoleAuthenticationError as err:
            raise ConfigEntryAuthFailed(
                "Contraseña de aplicación de Pi-hole no válida o sesión expirada"
            ) from err
        except PiHoleConnectionError as err:
            raise UpdateFailed(f"No se pudo consultar la API de Pi-hole: {err}") from err
        except HomeAssistantError as err:
            raise UpdateFailed(
                f"No se pudo registrar una consulta en el Logbook de Home Assistant: {err}"
            ) from err

    def _seed_clients(self, rows: Any) -> None:
        """Initialize client summaries from a query page without counting it."""
        identified = [
            (query_id, row)
            for row in rows
            if isinstance(row, dict) and (query_id := _query_id(row)) is not None
        ]
        for query_id, row in sorted(identified, key=lambda pair: pair[0]):
            self._update_client(row, query_id, count_as_new=False)

    def _update_query_rates(self, sampled_at: float) -> None:
        """Calculate per-client query rates since the previous successful poll."""
        elapsed = (
            sampled_at - self._last_rate_sample_at
            if self._last_rate_sample_at is not None
            else 0.0
        )
        current_counts: dict[str, tuple[int, int, int]] = {}
        for client_key, summary in self.client_data.items():
            counts = (
                int(summary.get("query_count", 0)),
                int(summary.get("allowed_count", 0)),
                int(summary.get("blocked_count", 0)),
            )
            current_counts[client_key] = counts
            previous = self._last_rate_counts.get(client_key, counts)
            rates = (
                tuple(
                    max(0, current - old) / elapsed
                    for current, old in zip(counts, previous, strict=True)
                )
                if elapsed > 0
                else (0.0, 0.0, 0.0)
            )
            summary["query_rate"] = rates[0]
            summary["allowed_rate"] = rates[1]
            summary["blocked_rate"] = rates[2]
        self._last_rate_counts = current_counts
        self._last_rate_sample_at = sampled_at

    def _update_client(
        self,
        query: dict[str, Any],
        query_id: int,
        *,
        count_as_new: bool,
    ) -> None:
        """Update one client's count and bounded query history."""
        key, client_name, client_ip = _client_key(query)
        summary = self.client_data.setdefault(
            key,
            {
                "client_name": client_name,
                "client_ip": client_ip,
                "query_count": 0,
                "allowed_count": 0,
                "blocked_count": 0,
                "last_query_id": 0,
                "recent_queries": [],
            },
        )
        summary["client_name"] = client_name
        summary["client_ip"] = client_ip

        recent = summary.setdefault("recent_queries", [])
        if not isinstance(recent, list):
            recent = []
        recent = [item for item in recent if isinstance(item, dict)]
        summary["recent_queries"] = recent
        if not any(item.get("id") == query_id for item in recent if isinstance(item, dict)):
            recent.append(_query_summary(query, query_id))
        recent.sort(key=lambda item: int(item.get("id", 0)))
        del recent[:-CLIENT_QUERY_HISTORY]

        try:
            last_query_id = int(summary.get("last_query_id", 0))
        except (TypeError, ValueError):
            last_query_id = 0
        if query_id >= last_query_id:
            summary["last_query_id"] = query_id
            summary["last_query"] = _query_summary(query, query_id)

        if count_as_new:
            summary["query_count"] = int(summary.get("query_count", 0)) + 1
            count_key = "blocked_count" if _blocked(query.get("status")) else "allowed_count"
            summary[count_key] = int(summary.get(count_key, 0)) + 1

    async def _log_query(self, row: dict[str, Any]) -> None:
        """Write one new query to Home Assistant Activity."""
        timestamp = row.get("time")
        try:
            event_time = datetime.fromtimestamp(float(timestamp), tz=UTC)
        except (TypeError, ValueError, OSError, OverflowError):
            event_time = datetime.now(UTC)
        name, message = _query_description(row)
        query_time = dt_util.as_local(event_time).isoformat(sep=" ", timespec="seconds")
        await self.hass.services.async_call(
            "logbook",
            "log",
            {
                "name": name,
                "message": f"Fecha/hora de Pi-hole: {query_time} · {message}",
                "domain": DOMAIN,
            },
            blocking=True,
        )

    async def _save_cursor(self) -> None:
        """Persist the high-water mark and bounded client summaries."""
        if self.cursor is not None:
            await self.store.async_save(
                {
                    "cursor": self.cursor,
                    "clients": self.client_data,
                }
            )
