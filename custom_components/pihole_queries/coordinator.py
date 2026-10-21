"""Coordinator that detects new Pi-hole query IDs and records Logbook events."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import PiHoleAuthenticationError, PiHoleClient, PiHoleConnectionError
from .const import (
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
    """Build a readable Logbook name and message with available query fields."""
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


class PiHoleQueryCoordinator(DataUpdateCoordinator[int]):
    """Poll the Pi-hole query endpoint and store the last processed query ID."""

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
        self._bootstrapped = False
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
        """Restore the last query ID persisted by this config entry."""
        saved = await self.store.async_load()
        if saved is not None:
            try:
                self.cursor = int(saved["cursor"])
                self._bootstrapped = True
            except (KeyError, TypeError, ValueError):
                _LOGGER.warning("Se ignoró un cursor local de Pi-hole no válido")

    async def _async_update_data(self) -> int:
        try:
            pages: list[dict[str, Any]] = []
            page = await self.client.async_get_queries(length=QUERY_BATCH_SIZE)
            pages.append(page)

            if not self._bootstrapped:
                # Establish a baseline from the newest page only. Scanning the
                # full pre-installation history would be costly and is not a backfill.
                ids = [value for row in page.get("queries", []) if (value := _query_id(row)) is not None]
                self.cursor = max(ids, default=0)
                self._bootstrapped = True
                await self._save_cursor()
                _LOGGER.info("Pi-hole query tracking initialized at query ID %s", self.cursor)
                return 0

            # The API returns newest records first. Continue backwards only while
            # a page may still contain IDs newer than our stored high-water mark.
            seen_cursors: set[int] = set()
            while True:
                rows = pages[-1].get("queries", [])
                ids = [value for row in rows if (value := _query_id(row)) is not None]
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
                pages.append(await self.client.async_get_queries(length=QUERY_BATCH_SIZE, cursor=next_cursor))

            rows = [row for result in pages for row in result.get("queries", []) if isinstance(row, dict)]
            identified = [(query_id, row) for row in rows if (query_id := _query_id(row)) is not None]
            if not identified:
                return 0

            newest_id = max(query_id for query_id, _ in identified)
            if self.cursor is not None and newest_id < self.cursor:
                # Pi-hole's query database was recreated or reset. Start from its
                # current head; IDs from the old database cannot be correlated.
                _LOGGER.warning("El identificador de consultas de Pi-hole se reinició; se establece un cursor nuevo")
                self.cursor = newest_id
                await self._save_cursor()
                return 0

            new_rows = sorted(
                ((query_id, row) for query_id, row in identified if query_id > (self.cursor or 0)),
                key=lambda pair: pair[0],
            )
            for query_id, row in new_rows:
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
                        "domain": "logbook",
                    },
                    blocking=True,
                )

            self.cursor = max(self.cursor or 0, newest_id)
            await self._save_cursor()
            return len(new_rows)
        except PiHoleAuthenticationError as err:
            raise ConfigEntryAuthFailed("Contraseña de aplicación de Pi-hole no válida o sesión expirada") from err
        except (PiHoleConnectionError, HomeAssistantError) as err:
            raise UpdateFailed("No se pudo consultar la API de Pi-hole") from err

    async def _save_cursor(self) -> None:
        """Persist the high-water mark independently from Pi-hole's short-lived SID."""
        if self.cursor is not None:
            await self.store.async_save({"cursor": self.cursor})
