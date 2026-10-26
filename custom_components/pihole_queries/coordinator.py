"""Poll Pi-hole queries, record Activity entries, and aggregate per-client data."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import PiHoleAuthenticationError, PiHoleClient, PiHoleConnectionError
from .classification import (
    CLASSIFIER_VERSION,
    classify_status,
    empty_classifier_counts,
)
from .const import (
    ACTIVITY_ALL,
    ACTIVITY_BLOCKED,
    ACTIVITY_NONE,
    CONF_ACTIVITY_MODE,
    CONF_RETENTION_MODE,
    CONF_SCAN_INTERVAL,
    DEFAULT_ACTIVITY_MODE,
    DEFAULT_RETENTION_MODE,
    DOMAIN,
    QUERY_BATCH_SIZE,
    RETENTION_AGGREGATE,
    RETENTION_DETAIL,
    CoverageReason,
)
from .validation import valid_classifier_epoch, valid_query_time_buckets

_LOGGER = logging.getLogger(__name__)

@dataclass
class _Checkpoint:
    """Coordinator state captured before a poll, used to roll back a failed commit."""

    cursor: int | None
    clients: dict[str, dict[str, Any]]
    coverage: str
    coverage_reason: str | None
    last_query_time: float | None
    bootstrapped: bool
    recovery_cursor: int | None
    awaiting_generation: bool


def _classification(status: Any) -> str:
    """Classify using the active immutable status map."""
    return classify_status(status, CLASSIFIER_VERSION)


def _origin_time(query: dict[str, Any]) -> datetime | None:
    """Return a trustworthy Pi-hole timestamp, excluding malformed/future values."""
    try:
        timestamp = float(query["time"])
        if not math.isfinite(timestamp) or timestamp > time.time() + 60:
            return None
        return datetime.fromtimestamp(timestamp, tz=UTC)
    except (KeyError, TypeError, ValueError, OSError, OverflowError):
        return None


def _query_id(query: dict[str, Any]) -> int | None:
    """Return a valid numeric query ID, if present."""
    value = query.get("id")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() and value >= 0 else None
    if isinstance(value, str):
        try:
            query_id = int(value.strip())
            return query_id if query_id >= 0 else None
        except ValueError:
            return None
    return None


def _timestamp_number(value: Any) -> float | None:
    """Return a finite epoch timestamp from Pi-hole's numeric query times."""
    if isinstance(value, bool):
        return None
    try:
        timestamp = float(value)
        return timestamp if math.isfinite(timestamp) else None
    except (TypeError, ValueError):
        return None


def _query_time_for_minute_window(query: dict[str, Any], observed_at: float) -> float | None:
    """Use Pi-hole's event time, falling back to observation time as in 0.6."""
    query_time = _timestamp_number(query.get("time"))
    if query_time is None or query_time < 0:
        return observed_at
    if query_time > observed_at:
        return None
    return query_time


def _add_query_time_bucket(summary: dict[str, Any], query_time: float, observed_at: float) -> None:
    """Add one anonymous query to a bounded source-time second bucket."""
    second = int(query_time)
    buckets = summary.setdefault("query_time_buckets", [])
    if not isinstance(buckets, list):
        buckets = []
    bucket = next(
        (
            item
            for item in buckets
            if isinstance(item, dict) and item.get("second") == second
        ),
        None,
    )
    if bucket is None:
        bucket = {"second": second, "count": 0}
        buckets.append(bucket)
    bucket["count"] += 1
    cutoff = int(observed_at) - 61
    summary["query_time_buckets"] = [
        item
        for item in buckets
        if isinstance(item, dict)
        and isinstance(item.get("second"), int)
        and item["second"] >= cutoff
    ][-61:]




def _blocked(status: Any) -> bool:
    """Return whether the exact status is explicitly classified as blocked."""
    return _classification(status) == "blocked"


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
    status = query.get("status") if "status" in query else "ausente"
    classification = _classification(status)
    verdict = {
        "allowed": "permitida",
        "blocked": "bloqueada",
        "provisional": "provisional",
    }[classification]
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


def _normalize_client_name(value: Any) -> str:
    """Normalize a fallback client name without changing its display label."""
    return " ".join(str(value or "").casefold().split())




def _client_key(
    query: dict[str, Any], existing_clients: dict[str, dict[str, Any]] | None = None
) -> tuple[str, str, str]:
    """Return a stable per-client key, display name, and IP address."""
    client = query.get("client") or {}
    if not isinstance(client, dict):
        client = {}
    client_ip = str(client.get("ip") or "").strip()
    client_name = str(client.get("name") or "").strip()
    normalized_name = _normalize_client_name(client_name)
    key = client_ip or normalized_name or "unknown"
    if not client_ip and normalized_name and existing_clients:
        # Keep the key already used by a migrated Store so its HA unique IDs
        # remain stable, while matching future name case/spacing variants.
        for existing_key, summary in existing_clients.items():
            if not isinstance(summary, dict):
                continue
            if summary.get("client_ip") not in (None, "", "Desconocida"):
                continue
            existing_name = summary.get("client_name") or existing_key
            if _normalize_client_name(existing_name) == normalized_name:
                key = str(existing_key)
                break
    return key, client_name or client_ip or "Cliente desconocido", client_ip or "Desconocida"


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
        self.retention_mode = entry.options.get(CONF_RETENTION_MODE, DEFAULT_RETENTION_MODE)
        self.activity_mode = entry.options.get(CONF_ACTIVITY_MODE, DEFAULT_ACTIVITY_MODE)
        if self.retention_mode not in {RETENTION_DETAIL, RETENTION_AGGREGATE}:
            raise HomeAssistantError("La política de conservación configurada no es válida")
        if self.activity_mode not in {ACTIVITY_ALL, ACTIVITY_BLOCKED, ACTIVITY_NONE}:
            raise HomeAssistantError("La política de Activity configurada no es válida")
        self.cursor: int | None = None
        self.client_data: dict[str, dict[str, Any]] = {}
        self.aggregate_pending: dict[str, dict[str, Any]] = {}
        self.aggregate_pending_cursor: int | None = None
        self.aggregate_pending_time: float | None = None
        self._bootstrapped = False
        self.coverage = "unknown"
        self.coverage_reason: str | None = CoverageReason.NO_BASELINE
        self.last_query_time: float | None = None
        # This is deliberately session-local: Store records ingestion state,
        # but does not prove Recorder persistence or retain a last-read claim.
        self.last_successful_read: datetime | None = None
        self.last_api_attempt_successful: bool | None = None
        # The one-minute source-time metric is withheld until a complete 60s
        # observation span has followed startup, when older Store formats may
        # lack enough timestamped rows to reconstruct that window.
        self._query_time_window_started_at: float | None = None
        self.query_time_window_ready = False
        self.awaiting_generation = False
        self.recovery_cursor: int | None = None
        self._client_store_invalid = False
        self._store_state_invalid = False
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
        self._client_store_invalid = False
        self._store_state_invalid = False
        saved = await self.store.async_load()
        if saved is None:
            return
        if not isinstance(saved, dict):
            self.coverage = "unknown"
            self.coverage_reason = CoverageReason.INVALID_LEGACY_STORE
            _LOGGER.warning("Se ignoró un Store local de Pi-hole con formato no válido")
            return
        pending = saved.get("aggregate_pending", {})
        if not isinstance(pending, dict):
            self._store_state_invalid = True
        else:
            for key, item in pending.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or len(key) > 255
                    or not isinstance(item, dict)
                    or not {"client_name", "client_ip", "counts", "buckets"}.issubset(item)
                    or set(item) - {
                        "client_name", "client_ip", "counts", "buckets", "query_time_buckets"
                    }
                    or not valid_query_time_buckets(item.get("query_time_buckets", []))
                ):
                    self._store_state_invalid = True
                    break
                counts = item.get("counts")
                if not isinstance(counts, dict) or set(counts) != {
                    "allowed",
                    "blocked",
                    "provisional",
                } or any(
                    isinstance(counts.get(name), bool)
                    or not isinstance(counts.get(name), int)
                    or counts[name] < 0
                    for name in ("allowed", "blocked", "provisional")
                ):
                    self._store_state_invalid = True
                    break
                buckets = item.get("buckets", [])
                if not isinstance(buckets, list) or len(buckets) > 61:
                    self._store_state_invalid = True
                    break
                if any(
                    not isinstance(bucket, dict)
                    or set(bucket)
                    != {
                        "second",
                        "classifier_version",
                        "allowed",
                        "blocked",
                        "provisional",
                    }
                    or isinstance(bucket.get("second"), bool)
                    or not isinstance(bucket.get("second"), int)
                    or isinstance(bucket.get("classifier_version"), bool)
                    or not isinstance(bucket.get("classifier_version"), int)
                    or not 1 <= bucket["classifier_version"] <= 2**31 - 1
                    or bucket["second"] < 0
                    or any(
                        isinstance(bucket.get(name), bool)
                        or not isinstance(bucket.get(name), int)
                        or bucket[name] < 0
                        for name in ("allowed", "blocked", "provisional")
                    )
                    for bucket in buckets
                ):
                    self._store_state_invalid = True
                    break
                for name in ("client_name", "client_ip"):
                    if item.get(name) is not None and not isinstance(item.get(name), str):
                        self._store_state_invalid = True
                        break
            if not self._store_state_invalid:
                self.aggregate_pending = pending
        raw_pending_cursor = saved.get("aggregate_pending_cursor")
        if raw_pending_cursor is not None:
            self.aggregate_pending_cursor = _query_id({"id": raw_pending_cursor})
            if self.aggregate_pending_cursor is None:
                self._store_state_invalid = True
        raw_pending_time = saved.get("aggregate_pending_time")
        if raw_pending_time is not None:
            if (
                isinstance(raw_pending_time, bool)
                or not isinstance(raw_pending_time, (int, float))
                or not math.isfinite(raw_pending_time)
            ):
                self._store_state_invalid = True
            else:
                self.aggregate_pending_time = float(raw_pending_time)
        cursor_valid = True
        try:
            self.cursor = _query_id({"id": saved["cursor"]})
            if self.cursor is None:
                raise ValueError("cursor inválido")
            self._bootstrapped = True
        except (KeyError, TypeError, ValueError):
            cursor_valid = False
            self.coverage = "unknown"
            saved_reason = saved.get("coverage_reason")
            self.coverage_reason = (
                saved_reason
                if isinstance(saved_reason, str)
                and saved_reason in {CoverageReason.INVALID_LEGACY_STORE, CoverageReason.INVALID_STORE_STATE}
                else CoverageReason.INVALID_LEGACY_CURSOR
            )
            _LOGGER.warning("Se ignoró un cursor local de Pi-hole no válido")
        coverage = saved.get("coverage")
        if "coverage" in saved and (
            not isinstance(coverage, str)
            or coverage not in {"unknown", "complete", "partial"}
        ):
            self._store_state_invalid = True
            self.coverage = "unknown"
            self.coverage_reason = CoverageReason.INVALID_STORE_STATE
        elif cursor_valid and coverage in {"unknown", "complete", "partial"}:
            self.coverage = coverage
            saved_reason = saved.get("coverage_reason")
            if saved_reason is not None and not isinstance(saved_reason, str):
                self._store_state_invalid = True
                self.coverage = "unknown"
                self.coverage_reason = CoverageReason.INVALID_STORE_STATE
            else:
                self.coverage_reason = saved_reason
        elif "coverage_reason" in saved and saved.get("coverage_reason") is not None and not isinstance(
            saved.get("coverage_reason"), str
        ):
            self._store_state_invalid = True
            self.coverage = "unknown"
            self.coverage_reason = CoverageReason.INVALID_STORE_STATE
        awaiting_generation = saved.get("awaiting_generation", False)
        if "awaiting_generation" in saved and not isinstance(awaiting_generation, bool):
            self._store_state_invalid = True
            self.coverage = "unknown"
            self.coverage_reason = CoverageReason.INVALID_STORE_STATE
        self.awaiting_generation = awaiting_generation is True
        raw_recovery_cursor = saved.get("recovery_cursor")
        self.recovery_cursor = (
            None
            if raw_recovery_cursor is None
            else _query_id({"id": raw_recovery_cursor})
        )
        if (
            "recovery_cursor" in saved
            and raw_recovery_cursor is not None
            and self.recovery_cursor is None
        ):
            self._store_state_invalid = True
            self.coverage = "unknown"
            self.coverage_reason = CoverageReason.INVALID_STORE_STATE
        if self.aggregate_pending and (
            self.recovery_cursor is None
            or self.aggregate_pending_cursor is None
            or (self.cursor is not None and self.aggregate_pending_cursor <= self.cursor)
            or (self.cursor is not None and self.recovery_cursor < self.cursor)
            or self.recovery_cursor >= self.aggregate_pending_cursor
        ):
            self._store_state_invalid = True
        if self.aggregate_pending and getattr(self, "retention_mode", RETENTION_DETAIL) != RETENTION_AGGREGATE:
            # The confirmed cursor never advanced for these deltas. Re-read them
            # under the newly selected detailed policy instead of applying a
            # hidden aggregate checkpoint to the Activity path.
            self.aggregate_pending = {}
            self.aggregate_pending_cursor = None
            self.aggregate_pending_time = None
            self.recovery_cursor = None
        if "last_query_time" not in saved or saved["last_query_time"] is None:
            self.last_query_time = None
        else:
            last_query_time = saved["last_query_time"]
            if (
                isinstance(last_query_time, bool)
                or not isinstance(last_query_time, (int, float))
                or not math.isfinite(last_query_time)
            ):
                self._store_state_invalid = True
                self.coverage = "unknown"
                self.coverage_reason = CoverageReason.INVALID_STORE_STATE
                self.last_query_time = None
            else:
                self.last_query_time = float(last_query_time)

        clients = saved.get("clients")
        if "clients" not in saved:
            clients = {}
            self.coverage = "unknown"
            if self.coverage_reason not in {
                CoverageReason.INVALID_LEGACY_CURSOR,
                CoverageReason.INVALID_LEGACY_STORE,
                CoverageReason.INVALID_STORE_STATE,
            }:
                self.coverage_reason = CoverageReason.MISSING_CLIENT_SUMMARIES
        if not isinstance(clients, dict):
            self._client_store_invalid = True
            self.client_data = {}
            self.coverage = "unknown"
            self.coverage_reason = CoverageReason.INVALID_CLIENT_SUMMARIES
            _LOGGER.warning("Se conservaron los contadores de Pi-hole porque los resúmenes locales no son válidos")
            return
        self.client_data = {}
        for key, value in clients.items():
            if not isinstance(value, dict):
                self._client_store_invalid = True
                continue
            summary = value
            # Older releases stored individual query details and observation
            # rows. Discard them as soon as the Store is loaded.
            for field in ("last_query", "last_query_id", "recent_queries", "recent_activity"):
                summary.pop(field, None)
            raw_count = summary.get("query_count")
            if (
                "query_count" not in summary
                or isinstance(raw_count, bool)
                or not isinstance(raw_count, int)
                or raw_count < 0
            ):
                self._client_store_invalid = True
            classifier_counts = summary.get("classifier_counts")
            if "classifier_counts" in summary:
                if not isinstance(classifier_counts, dict) or not classifier_counts:
                    self._client_store_invalid = True
                else:
                    clean_counts = {}
                    for epoch, epoch_counts in classifier_counts.items():
                        if (
                            not valid_classifier_epoch(epoch)
                            or not isinstance(epoch_counts, dict)
                            or set(epoch_counts) != {"allowed", "blocked", "provisional"}
                            or any(
                                isinstance(epoch_counts.get(name), bool)
                                or not isinstance(epoch_counts.get(name), int)
                                or epoch_counts[name] < 0
                                for name in ("allowed", "blocked", "provisional")
                            )
                        ):
                            self._client_store_invalid = True
                            continue
                        clean_counts[epoch] = epoch_counts
                    if len(clean_counts) != len(classifier_counts):
                        summary["classifier_counts"] = clean_counts
            classifier_rates = summary.get("classifier_rates")
            if "classifier_rates" in summary:
                if not isinstance(classifier_rates, dict):
                    self._client_store_invalid = True
                    summary["classifier_rates"] = {}
                else:
                    clean_rates = {}
                    for epoch, epoch_rates in classifier_rates.items():
                        if (
                            not valid_classifier_epoch(epoch)
                            or not isinstance(epoch_rates, dict)
                            or set(epoch_rates) != {"allowed", "blocked", "provisional"}
                            or any(
                                isinstance(epoch_rates.get(name), bool)
                                or not isinstance(epoch_rates.get(name), int)
                                or epoch_rates[name] < 0
                                for name in ("allowed", "blocked", "provisional")
                            )
                        ):
                            self._client_store_invalid = True
                            continue
                        clean_rates[epoch] = epoch_rates
                    if len(clean_rates) != len(classifier_rates):
                        summary["classifier_rates"] = clean_rates
            if "query_time_buckets" in summary and not valid_query_time_buckets(
                summary["query_time_buckets"]
            ):
                self._client_store_invalid = True
            if "activity_buckets" in summary:
                buckets = summary["activity_buckets"]
                if not isinstance(buckets, list) or len(buckets) > 61:
                    self._client_store_invalid = True
                else:
                    seen_buckets: set[tuple[int, int]] = set()
                    for bucket in buckets:
                        if (
                            not isinstance(bucket, dict)
                            or set(bucket)
                            != {
                                "second",
                                "classifier_version",
                                "allowed",
                                "blocked",
                                "provisional",
                            }
                            or isinstance(bucket.get("second"), bool)
                            or not isinstance(bucket.get("second"), int)
                            or isinstance(bucket.get("classifier_version"), bool)
                            or not isinstance(bucket.get("classifier_version"), int)
                            or any(
                                isinstance(bucket.get(name), bool)
                                or not isinstance(bucket.get(name), int)
                                or bucket[name] < 0
                                for name in ("allowed", "blocked", "provisional")
                            )
                        ):
                            self._client_store_invalid = True
                            break
                        bucket_key = (bucket["second"], bucket["classifier_version"])
                        if bucket_key in seen_buckets:
                            self._client_store_invalid = True
                            break
                        seen_buckets.add(bucket_key)
            summary.setdefault("activity_buckets", [])
            summary.setdefault("query_time_buckets", [])
            self.client_data[str(key)] = summary
        if getattr(self, "_client_store_invalid", False) or getattr(
            self, "_store_state_invalid", False
        ):
            self.coverage = "unknown"
            self.coverage_reason = (
                CoverageReason.INVALID_STORE_STATE
                if getattr(self, "_store_state_invalid", False)
                else CoverageReason.INVALID_CLIENT_SUMMARIES
            )
            _LOGGER.warning("Se conservaron los contadores de Pi-hole porque hay agregados locales no válidos")
        for summary in self.client_data.values():
            summary["classifier_version"] = CLASSIFIER_VERSION
            counts = summary.get("classifier_counts")
            if not isinstance(counts, dict):
                counts = {}
                summary["classifier_counts"] = counts
            counts.setdefault(str(CLASSIFIER_VERSION), empty_classifier_counts())
            rates = summary.get("classifier_rates")
            if not isinstance(rates, dict):
                rates = {}
                summary["classifier_rates"] = rates
            rates.setdefault(str(CLASSIFIER_VERSION), empty_classifier_counts())

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch data and update summaries from the Pi-hole API."""
        return await self._async_update_data_snapshot()

    def _data_snapshot(self) -> dict[str, dict[str, Any]]:
        """Copy the bounded numeric client summaries for Home Assistant."""
        snapshot: dict[str, dict[str, Any]] = {}
        obsolete_fields = {"last_query", "last_query_id", "recent_queries", "recent_activity"}
        for key, summary in self.client_data.items():
            if not isinstance(summary, dict):
                continue
            snapshot[key] = deepcopy(
                {field: value for field, value in summary.items() if field not in obsolete_fields}
            )
        return snapshot

    async def _async_get_queries(self, *, length: int, cursor: int | None = None) -> dict[str, Any]:
        """Read Pi-hole queries and record API success independently of later work."""
        try:
            response = await self.client.async_get_queries(length=length, cursor=cursor)
        except Exception:
            self.last_api_attempt_successful = False
            raise
        self.last_api_attempt_successful = True
        self.last_successful_read = dt_util.utcnow()
        return response

    async def _async_update_data_snapshot(self) -> dict[str, dict[str, Any]]:
        """Fetch new queries and return a snapshot of per-client summaries."""
        self.coverage = getattr(self, "coverage", "unknown")
        self.coverage_reason = getattr(self, "coverage_reason", CoverageReason.LEGACY_CURSOR)
        self.last_query_time = getattr(self, "last_query_time", None)
        self.awaiting_generation = getattr(self, "awaiting_generation", False)
        self.recovery_cursor = getattr(self, "recovery_cursor", None)
        checkpoint = self._checkpoint()
        try:
            if getattr(self, "retention_mode", RETENTION_DETAIL) == RETENTION_AGGREGATE:
                return await self._async_update_aggregate_data(checkpoint=checkpoint)
            pages: list[dict[str, Any]] = [
                await self._async_get_queries(length=QUERY_BATCH_SIZE)
            ]
            if getattr(self, "_client_store_invalid", False) or getattr(
                self, "_store_state_invalid", False
            ):
                self.coverage = "unknown"
                self.coverage_reason = (
                    CoverageReason.INVALID_STORE_STATE
                    if getattr(self, "_store_state_invalid", False)
                    else CoverageReason.INVALID_CLIENT_SUMMARIES
                )
                raise UpdateFailed(
                    "No se pueden actualizar los contadores porque los datos locales no son válidos"
                )
            if self.awaiting_generation:
                # Keep checking API health, but attach a new source generation
                # before these IDs can enter cumulative series.
                return self._data_snapshot()

            if not self._bootstrapped:
                # Seed client entities from the current page, without logging or
                # counting old queries as newly observed.
                rows = pages[0].get("queries", [])
                ids = [(query_id, row) for row in rows if isinstance(row, dict)
                       and (query_id := _query_id(row)) is not None]
                self._seed_clients(rows)
                if not ids:
                    self.coverage = "unknown"
                    if self.coverage_reason not in {
                        CoverageReason.INVALID_LEGACY_CURSOR,
                        CoverageReason.INVALID_LEGACY_STORE,
                    }:
                        self.coverage_reason = CoverageReason.BASELINE_UNVERIFIED
                    return self._data_snapshot()
                self.cursor = max((query_id for query_id, _ in ids), default=0)
                self._bootstrapped = True
                self.recovery_cursor = None
                invalid_legacy_cursor = self.coverage_reason in {
                    CoverageReason.INVALID_LEGACY_CURSOR,
                    CoverageReason.INVALID_LEGACY_STORE,
                }
                self.coverage = (
                    "unknown"
                    if invalid_legacy_cursor
                    else "complete" if ids and len(ids) == len(rows) else "unknown"
                )
                self.coverage_reason = (
                    None
                    if self.coverage == "complete"
                    else self.coverage_reason if invalid_legacy_cursor else CoverageReason.BASELINE_UNVERIFIED
                )
                newest = max(ids, default=(None, None), key=lambda item: item[0])[1]
                self.last_query_time = _timestamp_number(newest.get("time")) if newest else None
                self._update_minute_query_counts(time.time())
                await self._commit_or_rollback(checkpoint, restore_awaiting=True)
                _LOGGER.info("Pi-hole query tracking initialized at query ID %s", self.cursor)
                return self._data_snapshot()

            # Pi-hole returns newest records first. Follow cursors until the
            # previously stored high-water mark is reached.
            starting_cursor = self.cursor
            if self.recovery_cursor is not None and starting_cursor is not None:
                probe = await self._async_get_queries(
                    length=QUERY_BATCH_SIZE, cursor=self.recovery_cursor
                )
                probe_rows = probe.get("queries", [])
                probe_ids = [
                    query_id
                    for row in probe_rows
                    if isinstance(row, dict) and (query_id := _query_id(row)) is not None
                ] if isinstance(probe_rows, list) else []
                if not probe_ids or min(probe_ids) > starting_cursor:
                    if probe_ids:
                        next_recovery_cursor = min(probe_ids) - 1
                        if next_recovery_cursor != self.recovery_cursor:
                            self.recovery_cursor = next_recovery_cursor
                    self.coverage = "unknown"
                    self.coverage_reason = CoverageReason.CURSOR_NOT_REACHED
                    self._update_minute_query_counts(time.time())
                    await self._commit_or_rollback(checkpoint)
                    return self._data_snapshot()
                self.recovery_cursor = None

            seen_cursors: set[int] = set()
            reached_cursor = False
            malformed_rows = False
            pagination_stalled = False
            last_requested_cursor: int | None = None
            while True:
                rows = pages[-1].get("queries", [])
                if not isinstance(rows, list):
                    malformed_rows = True
                    break
                ids = [
                    value
                    for row in rows
                    if isinstance(row, dict) and (value := _query_id(row)) is not None
                ]
                if len(ids) != len(rows):
                    malformed_rows = True
                if not rows:
                    break
                if not ids:
                    malformed_rows = True
                    break
                if starting_cursor is not None and min(ids) <= starting_cursor:
                    reached_cursor = True
                    break
                # The Pi-hole v6 instance returns `cursor` as the newest ID in
                # the current page; passing that value back repeats the page.
                # Its cursor parameter is inclusive, so request the page before
                # the oldest ID we just received.
                next_cursor = min(ids) - 1
                if next_cursor < 0:
                    break
                if next_cursor in seen_cursors:
                    pagination_stalled = True
                    break
                seen_cursors.add(next_cursor)
                last_requested_cursor = next_cursor
                pages.append(
                    await self._async_get_queries(
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
            # Pages may overlap at their inclusive cursor boundary. Keep the
            # first response for each ID so counters, Activity, and rates agree.
            identified_by_id: dict[int, dict[str, Any]] = {}
            for query_id, row in identified:
                identified_by_id.setdefault(query_id, row)
            identified = list(identified_by_id.items())
            if not identified:
                self._update_minute_query_counts(time.time())
                if malformed_rows:
                    self.coverage = "partial"
                    self.coverage_reason = CoverageReason.INVALID_QUERY_IDS
                elif not pages[0].get("queries"):
                    self.coverage = "unknown"
                    self.coverage_reason = CoverageReason.EMPTY_HISTORY
                await self._commit_or_rollback(checkpoint)
                return self._data_snapshot()

            newest_id = max(query_id for query_id, _ in identified)
            if self.cursor is not None and newest_id < self.cursor:
                # Attach a new source generation before ingestion can
                # resume; otherwise cumulative series would combine databases.
                _LOGGER.warning(
                    "El identificador de consultas de Pi-hole se reinició; se establece un cursor nuevo"
                )
                self.cursor = newest_id
                self.coverage = "unknown"
                self.coverage_reason = CoverageReason.QUERY_ID_RESET
                self.awaiting_generation = True
                self.recovery_cursor = None
                self.last_query_time = None
                self._update_minute_query_counts(time.time())
                await self._commit_or_rollback(checkpoint, restore_awaiting=True)
                return self._data_snapshot()

            if not self.client_data:
                # Discover clients on upgrade from older releases without
                # changing their observed counters or emitting old Activity.
                self._seed_clients(pages[0].get("queries", []))

            previous_query_time = self.last_query_time
            earliest_timestamp = _timestamp_number(pages[0].get("earliest_timestamp"))
            retention_truncated = (
                earliest_timestamp is not None
                and previous_query_time is not None
                and earliest_timestamp > previous_query_time
            )
            if retention_truncated:
                self.coverage = "partial"
                self.coverage_reason = CoverageReason.RETENTION_TRUNCATION
                self.recovery_cursor = None
            elif pagination_stalled:
                self.coverage = "partial"
                self.coverage_reason = CoverageReason.PAGINATION_STALLED
                self.recovery_cursor = last_requested_cursor
            elif malformed_rows and not reached_cursor:
                self.coverage = "partial"
                self.coverage_reason = CoverageReason.INVALID_QUERY_IDS
                self.recovery_cursor = last_requested_cursor
            elif malformed_rows:
                self.coverage = "partial"
                self.coverage_reason = CoverageReason.INVALID_QUERY_IDS
            elif (
                reached_cursor
                and self.coverage != "partial"
                and self.coverage_reason
                not in {CoverageReason.INVALID_CLIENT_SUMMARIES, CoverageReason.MISSING_CLIENT_SUMMARIES}
            ):
                # A prior unknown legacy cursor becomes a baseline only once
                # this API walk demonstrably reaches it.
                self.coverage = "complete"
                self.coverage_reason = None
            elif not reached_cursor and self.coverage != "partial":
                # Do not advance the confirmed cursor when the page walk ended
                # without reaching it. Retain a retry point so transient empty
                # pages cannot turn a recoverable gap into permanent loss.
                self.coverage = "unknown"
                self.coverage_reason = CoverageReason.CURSOR_NOT_REACHED
                self.recovery_cursor = last_requested_cursor

            if not reached_cursor and not retention_truncated:
                self._update_minute_query_counts(time.time())
                await self._commit_or_rollback(checkpoint)
                return self._data_snapshot()

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
                    if self._should_log_query(row):
                        await self._log_query(row)
                    self._update_client(row, query_id, count_as_new=True)
                    # If a later Activity write fails, persist the successfully
                    # logged prefix so it is not counted or logged again.
                    self.cursor = query_id
                    self.last_query_time = _timestamp_number(row.get("time"))
            except asyncio.CancelledError:
                # Cancellation may happen after a Logbook write. Persist the
                # confirmed prefix when possible, then propagate cancellation.
                if self.coverage != "partial":
                    self.coverage = "unknown"
                    self.coverage_reason = CoverageReason.BATCH_INTERRUPTED
                await self._commit_or_rollback(checkpoint)
                raise
            except HomeAssistantError:
                if self.coverage != "partial":
                    self.coverage = "unknown"
                    self.coverage_reason = CoverageReason.BATCH_INTERRUPTED
                await self._commit_or_rollback(checkpoint)
                raise

            self.cursor = max(self.cursor or 0, newest_id)
            self._update_minute_query_counts(time.time())
            if reached_cursor and self.coverage_reason == CoverageReason.BATCH_INTERRUPTED:
                self.coverage = "complete"
                self.coverage_reason = None
            await self._commit_or_rollback(checkpoint)
            return self._data_snapshot()
        except PiHoleAuthenticationError as err:
            raise ConfigEntryAuthFailed(
                "Contraseña de aplicación de Pi-hole no válida o sesión expirada"
            ) from err
        except PiHoleConnectionError as err:
            raise UpdateFailed(f"No se pudo consultar la API de Pi-hole: {err}") from err
        except UpdateFailed:
            raise
        except HomeAssistantError as err:
            raise UpdateFailed(
                f"No se pudo registrar una consulta en el Logbook de Home Assistant: {err}"
            ) from err

    def _checkpoint(self) -> _Checkpoint:
        """Capture the state to restore if the Store cannot confirm this poll."""
        return _Checkpoint(
            cursor=self.cursor,
            clients=deepcopy(self.client_data),
            coverage=self.coverage,
            coverage_reason=self.coverage_reason,
            last_query_time=self.last_query_time,
            bootstrapped=self._bootstrapped,
            recovery_cursor=self.recovery_cursor,
            awaiting_generation=self.awaiting_generation,
        )

    def _restore(
        self,
        checkpoint: _Checkpoint,
        *,
        restore_awaiting: bool,
        aggregate_state: tuple[dict[str, dict[str, Any]], int | None, float | None] | None,
    ) -> None:
        self.cursor = checkpoint.cursor
        self.client_data = checkpoint.clients
        self.coverage = checkpoint.coverage
        self.coverage_reason = checkpoint.coverage_reason
        self.last_query_time = checkpoint.last_query_time
        self._bootstrapped = checkpoint.bootstrapped
        self.recovery_cursor = checkpoint.recovery_cursor
        if restore_awaiting:
            self.awaiting_generation = checkpoint.awaiting_generation
        if aggregate_state is not None:
            (
                self.aggregate_pending,
                self.aggregate_pending_cursor,
                self.aggregate_pending_time,
            ) = aggregate_state

    async def _commit_or_rollback(
        self,
        checkpoint: _Checkpoint,
        *,
        restore_awaiting: bool = False,
        aggregate_state: tuple[dict[str, dict[str, Any]], int | None, float | None] | None = None,
    ) -> None:
        """Commit the cursor and summaries together, restoring memory on Store failure."""
        try:
            await self._save_cursor()
        except asyncio.CancelledError:
            self._restore(
                checkpoint, restore_awaiting=restore_awaiting, aggregate_state=aggregate_state
            )
            raise
        except Exception as err:
            self._restore(
                checkpoint, restore_awaiting=restore_awaiting, aggregate_state=aggregate_state
            )
            raise UpdateFailed("No se pudo confirmar el progreso de consultas en el Store") from err

    async def _async_update_aggregate_data(
        self,
        *,
        checkpoint: _Checkpoint,
    ) -> dict[str, dict[str, Any]]:
        """Stream single rows and persist only aggregate deltas while recovering."""
        original_pending_state = (
            deepcopy(self.aggregate_pending),
            self.aggregate_pending_cursor,
            self.aggregate_pending_time,
        )
        pending = deepcopy(self.aggregate_pending)
        pending_cursor = self.aggregate_pending_cursor
        pending_time = self.aggregate_pending_time
        for summary in self.client_data.values():
            for field in ("last_query", "last_query_id", "recent_queries", "recent_activity"):
                summary.pop(field, None)

        async def commit(*, awaiting_generation: bool | None = None) -> None:
            self.aggregate_pending = pending
            self.aggregate_pending_cursor = pending_cursor
            self.aggregate_pending_time = pending_time
            self._update_minute_query_counts(time.time())
            await self._commit_or_rollback(
                checkpoint,
                restore_awaiting=awaiting_generation is not None,
                aggregate_state=original_pending_state,
            )

        if getattr(self, "_client_store_invalid", False) or getattr(
            self, "_store_state_invalid", False
        ):
            self.coverage = "unknown"
            self.coverage_reason = CoverageReason.INVALID_STORE_STATE if self._store_state_invalid else CoverageReason.INVALID_CLIENT_SUMMARIES
            raise UpdateFailed("No se pueden actualizar los contadores porque los datos locales no son válidos")

        latest = await self._async_get_queries(length=1)
        latest_rows = latest.get("queries", []) if isinstance(latest, dict) else None
        if not isinstance(latest_rows, list):
            self.coverage = "partial"
            self.coverage_reason = CoverageReason.INVALID_QUERY_IDS
            await commit()
            return self._data_snapshot()
        latest_identified = [
            (query_id, row)
            for row in latest_rows
            if isinstance(row, dict) and (query_id := _query_id(row)) is not None
        ]
        if len(latest_identified) != len(latest_rows) or len(latest_rows) > 1:
            self.coverage = "partial"
            self.coverage_reason = CoverageReason.INVALID_QUERY_IDS
            await commit()
            return self._data_snapshot()
        if not latest_identified:
            if not self._bootstrapped:
                self.coverage = "unknown"
                self.coverage_reason = CoverageReason.BASELINE_UNVERIFIED
            elif pending:
                self.coverage = "unknown"
                self.coverage_reason = CoverageReason.CURSOR_NOT_REACHED
            else:
                self.coverage = "unknown"
                self.coverage_reason = CoverageReason.EMPTY_HISTORY
            await commit()
            return self._data_snapshot()

        latest_id, latest_row = latest_identified[0]
        if not self._bootstrapped:
            # Baseline discovers only the newest client's identity; historical
            # rows are not imported as newly observed queries.
            self._update_client(latest_row, latest_id, count_as_new=False)
            self.cursor = latest_id
            self._bootstrapped = True
            self.recovery_cursor = None
            self.last_query_time = _timestamp_number(latest_row.get("time"))
            self.coverage = "complete"
            self.coverage_reason = None
            await commit()
            return self._data_snapshot()

        if self.awaiting_generation:
            await commit()
            return self._data_snapshot()

        if checkpoint.cursor is not None and latest_id < checkpoint.cursor:
            _LOGGER.warning("El identificador de consultas de Pi-hole se reinició; se establece un cursor nuevo")
            self.cursor = latest_id
            self.coverage = "unknown"
            self.coverage_reason = CoverageReason.QUERY_ID_RESET
            self.awaiting_generation = True
            self.recovery_cursor = None
            pending = {}
            pending_cursor = None
            pending_time = None
            await commit(awaiting_generation=checkpoint.awaiting_generation)
            return self._data_snapshot()

        if latest_id <= (checkpoint.cursor or 0) and not pending:
            self.recovery_cursor = None
            await commit()
            return self._data_snapshot()

        earliest = _timestamp_number(latest.get("earliest_timestamp"))
        truncated = (
            earliest is not None
            and checkpoint.last_query_time is not None
            and earliest > checkpoint.last_query_time
        )
        if truncated:
            self.coverage = "partial"
            self.coverage_reason = CoverageReason.RETENTION_TRUNCATION

        request_cursor = self.recovery_cursor
        response = None if request_cursor is not None else latest
        last_seen = pending_cursor
        processed = 0
        reached_cursor = False
        exhausted = False
        malformed = False
        while processed < QUERY_BATCH_SIZE:
            if response is None:
                try:
                    response = await self._async_get_queries(
                        length=1, cursor=request_cursor
                    )
                except (PiHoleAuthenticationError, PiHoleConnectionError):
                    self.recovery_cursor = request_cursor
                    self.coverage = "unknown"
                    self.coverage_reason = CoverageReason.CURSOR_NOT_REACHED
                    await commit()
                    raise
            rows = response.get("queries", []) if isinstance(response, dict) else None
            if not isinstance(rows, list) or len(rows) > 1:
                malformed = True
                break
            if not rows:
                exhausted = True
                break
            row = rows[0]
            query_id = _query_id(row) if isinstance(row, dict) else None
            if query_id is None:
                malformed = True
                break
            if query_id <= (checkpoint.cursor or 0):
                reached_cursor = True
                break
            if last_seen is not None and query_id >= last_seen:
                malformed = True
                break

            client = row.get("client")
            minimal_query = {"client": client if isinstance(client, dict) else {}}
            key, client_name, client_ip = _client_key(minimal_query, self.client_data)
            item = pending.setdefault(
                key,
                {
                    "client_name": client_name,
                    "client_ip": client_ip,
                    "counts": empty_classifier_counts(),
                    "buckets": [],
                    "query_time_buckets": [],
                },
            )
            classification = _classification(row.get("status"))
            item["counts"][classification] += 1
            observed_at = time.time()
            query_time = _query_time_for_minute_window(row, observed_at)
            if query_time is None:
                self._query_time_window_started_at = None
                self.query_time_window_ready = False
            else:
                _add_query_time_bucket(item, query_time, observed_at)
            observed_second = int(observed_at)
            bucket = next(
                (
                    value
                    for value in item["buckets"]
                    if value["second"] == observed_second
                    and value["classifier_version"] == CLASSIFIER_VERSION
                ),
                None,
            )
            if bucket is None:
                bucket = {
                    "second": observed_second,
                    "classifier_version": CLASSIFIER_VERSION,
                    **empty_classifier_counts(),
                }
                item["buckets"].append(bucket)
            bucket[classification] += 1
            cutoff = observed_second - 60
            item["buckets"] = [
                value for value in item["buckets"] if value["second"] >= cutoff
            ][-61:]
            if pending_cursor is None or query_id > pending_cursor:
                # Cursor order, rather than response iteration order, selects
                # the timestamp associated with the confirmed high-water ID.
                pending_cursor = query_id
                pending_time = _timestamp_number(row.get("time"))
            last_seen = query_id
            request_cursor = query_id - 1
            processed += 1
            response = None

        if malformed:
            self.coverage = "partial"
            self.coverage_reason = CoverageReason.PAGINATION_STALLED
        if not reached_cursor and (not truncated or not exhausted or malformed):
            self.recovery_cursor = request_cursor
            if malformed:
                self.coverage = "partial"
                self.coverage_reason = CoverageReason.PAGINATION_STALLED
            elif truncated:
                self.coverage = "partial"
                self.coverage_reason = CoverageReason.RETENTION_TRUNCATION
            else:
                self.coverage = "unknown"
                self.coverage_reason = CoverageReason.CURSOR_NOT_REACHED
            await commit()
            return self._data_snapshot()

        # A proven boundary (or confirmed retention truncation) allows these
        # anonymous numeric deltas to become visible cumulative counts.
        for key, item in pending.items():
            summary = self.client_data.setdefault(
                key,
                {
                    "client_name": item["client_name"],
                    "client_ip": item["client_ip"],
                    "query_count": 0,
                    "allowed_count": 0,
                    "blocked_count": 0,
                    "classifier_version": CLASSIFIER_VERSION,
                    "classifier_counts": {str(CLASSIFIER_VERSION): empty_classifier_counts()},
                    "classifier_rates": {str(CLASSIFIER_VERSION): empty_classifier_counts()},
                    "queries_observed_last_minute": 0,
                    "queries_last_minute": 0,
                    "query_time_buckets": [],
                    "activity_buckets": [],
                },
            )
            summary["client_name"] = item["client_name"]
            summary["client_ip"] = item["client_ip"]
            counts = summary.setdefault("classifier_counts", {}).setdefault(
                str(CLASSIFIER_VERSION), empty_classifier_counts()
            )
            for classification, count in item["counts"].items():
                counts[classification] = int(counts.get(classification, 0)) + count
            summary["query_count"] = int(summary.get("query_count", 0)) + sum(
                item["counts"].values()
            )
            summary["classifier_version"] = CLASSIFIER_VERSION
            if "query_time_buckets" not in item:
                self._query_time_window_started_at = None
                self.query_time_window_ready = False
            for source_bucket in item.get("query_time_buckets", []):
                target = summary.setdefault("query_time_buckets", [])
                existing = next(
                    (
                        bucket
                        for bucket in target
                        if isinstance(bucket, dict)
                        and bucket.get("second") == source_bucket["second"]
                    ),
                    None,
                )
                if existing is None:
                    target.append(dict(source_bucket))
                else:
                    existing["count"] += source_bucket["count"]
            if "query_time_buckets" in summary:
                summary["query_time_buckets"] = sorted(
                    summary["query_time_buckets"], key=lambda bucket: bucket["second"]
                )[-61:]
            # Recovery can overlap the already observed 60-second window. Keep
            # one bucket per second/version and add the anonymous totals so the
            # Store scrub does not mistake valid data for a malformed duplicate.
            merged_buckets = {
                (bucket["second"], bucket["classifier_version"]): dict(bucket)
                for bucket in summary.setdefault("activity_buckets", [])
            }
            for bucket in item["buckets"]:
                identity = (bucket["second"], bucket["classifier_version"])
                existing = merged_buckets.get(identity)
                if existing is None:
                    merged_buckets[identity] = dict(bucket)
                    continue
                for classification in ("allowed", "blocked", "provisional"):
                    existing[classification] += bucket[classification]
            summary["activity_buckets"] = sorted(
                merged_buckets.values(),
                key=lambda bucket: (bucket["second"], bucket["classifier_version"]),
            )[-61:]
        if pending_cursor is not None:
            self.cursor = max(self.cursor or 0, pending_cursor)
        if pending_time is not None:
            self.last_query_time = pending_time
        pending = {}
        pending_cursor = None
        pending_time = None
        self.aggregate_pending = pending
        self.aggregate_pending_cursor = None
        self.aggregate_pending_time = None
        self.recovery_cursor = None
        if not truncated and reached_cursor and self.coverage != "partial":
            self.coverage = "complete"
            self.coverage_reason = None
        await commit()
        return self._data_snapshot()

    def _seed_clients(self, rows: Any) -> None:
        """Initialize client summaries from a query page without counting it."""
        identified = [
            (query_id, row)
            for row in rows
            if isinstance(row, dict) and (query_id := _query_id(row)) is not None
        ]
        for query_id, row in sorted(identified, key=lambda pair: pair[0]):
            self._update_client(row, query_id, count_as_new=False)

    def _update_minute_query_counts(self, sampled_at: float) -> None:
        """Update rolling counters from bounded numeric buckets."""
        if getattr(self, "coverage", "unknown") != "complete" or getattr(
            self, "awaiting_generation", False
        ):
            self._query_time_window_started_at = None
            self.query_time_window_ready = False
        else:
            if getattr(self, "_query_time_window_started_at", None) is None:
                self._query_time_window_started_at = sampled_at
            self.query_time_window_ready = (
                sampled_at - self._query_time_window_started_at > 60
            )
        self._update_bucket_minute_counts(sampled_at)

    def _update_bucket_minute_counts(self, sampled_at: float) -> None:
        """Roll up bounded numeric second buckets without query-level rows."""
        window_start = int(sampled_at - 60)
        window_end = int(sampled_at)
        for summary in self.client_data.values():
            raw_buckets = summary.get("activity_buckets", [])
            if not isinstance(raw_buckets, list):
                raw_buckets = []
            buckets: list[dict[str, int]] = []
            rates = empty_classifier_counts()
            try:
                active_version = int(summary.get("classifier_version", CLASSIFIER_VERSION))
            except (TypeError, ValueError):
                active_version = CLASSIFIER_VERSION
            for bucket in raw_buckets:
                if not isinstance(bucket, dict):
                    continue
                second = bucket.get("second")
                version = bucket.get("classifier_version")
                if (
                    isinstance(second, bool)
                    or not isinstance(second, int)
                    or not window_start <= second <= window_end
                    or isinstance(version, bool)
                    or not isinstance(version, int)
                ):
                    continue
                clean = {"second": second, "classifier_version": version}
                valid = True
                for classification in ("allowed", "blocked", "provisional"):
                    count = bucket.get(classification)
                    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                        valid = False
                        break
                    clean[classification] = count
                if not valid:
                    continue
                buckets.append(clean)
                if version == active_version:
                    for classification in rates:
                        rates[classification] += clean[classification]
            summary["activity_buckets"] = buckets[-61:]
            summary["queries_observed_last_minute"] = sum(
                sum(bucket[name] for name in ("allowed", "blocked", "provisional"))
                for bucket in buckets
            )
            source_buckets = summary.get("query_time_buckets", [])
            if not isinstance(source_buckets, list):
                source_buckets = []
            source_buckets = [
                bucket
                for bucket in source_buckets
                if isinstance(bucket, dict)
                and isinstance(bucket.get("second"), int)
                and not isinstance(bucket.get("second"), bool)
                and window_start <= bucket["second"] <= window_end
                and isinstance(bucket.get("count"), int)
                and not isinstance(bucket.get("count"), bool)
                and bucket["count"] >= 0
            ][-61:]
            summary["query_time_buckets"] = source_buckets
            summary["queries_last_minute"] = sum(
                bucket["count"] for bucket in source_buckets
            )
            saved_rates = summary.get("classifier_rates")
            if not isinstance(saved_rates, dict):
                saved_rates = {}
            saved_rates[str(active_version)] = rates
            summary["classifier_rates"] = saved_rates

    def _should_log_query(self, query: dict[str, Any]) -> bool:
        """Apply the Activity policy without changing query accounting."""
        if getattr(self, "retention_mode", RETENTION_DETAIL) == RETENTION_AGGREGATE:
            return False
        activity_mode = getattr(self, "activity_mode", ACTIVITY_ALL)
        if activity_mode == ACTIVITY_ALL:
            return True
        if activity_mode == ACTIVITY_BLOCKED:
            return _classification(query.get("status")) == "blocked"
        return False

    def _update_client(
        self,
        query: dict[str, Any],
        query_id: int,
        *,
        count_as_new: bool,
    ) -> None:
        """Update one client's count and bounded query history."""
        key, client_name, client_ip = _client_key(query, self.client_data)
        summary = self.client_data.setdefault(
            key,
            {
                "client_name": client_name,
                "client_ip": client_ip,
                "query_count": 0,
                "allowed_count": 0,
                "blocked_count": 0,
                "queries_last_minute": 0,
                "allowed_last_minute": 0,
                "blocked_last_minute": 0,
                "activity_buckets": [],
                "query_time_buckets": [],
                "classifier_version": CLASSIFIER_VERSION,
                "classifier_counts": {str(CLASSIFIER_VERSION): empty_classifier_counts()},
                "classifier_rates": {str(CLASSIFIER_VERSION): empty_classifier_counts()},
                "queries_observed_last_minute": 0,
            },
        )
        summary["client_name"] = client_name
        summary["client_ip"] = client_ip
        summary["classifier_version"] = CLASSIFIER_VERSION
        all_counts = summary.get("classifier_counts")
        if not isinstance(all_counts, dict):
            all_counts = {}
            summary["classifier_counts"] = all_counts
        active_counts = all_counts.setdefault(str(CLASSIFIER_VERSION), empty_classifier_counts())
        if not isinstance(active_counts, dict):
            active_counts = empty_classifier_counts()
            all_counts[str(CLASSIFIER_VERSION)] = active_counts
        for classification in ("allowed", "blocked", "provisional"):
            try:
                active_counts[classification] = max(0, int(active_counts.get(classification, 0)))
            except (TypeError, ValueError):
                active_counts[classification] = 0

        if count_as_new:
            summary["query_count"] = int(summary.get("query_count", 0)) + 1
            classification = _classification(query.get("status"))
            active_counts[classification] += 1
            observed_at = time.time()
            query_time = _query_time_for_minute_window(query, observed_at)
            if query_time is None:
                # A future FTL clock cannot support a trustworthy rolling
                # window. Re-establish a full observation span before zeroes
                # become visible again.
                self._query_time_window_started_at = None
                self.query_time_window_ready = False
            else:
                _add_query_time_bucket(summary, query_time, observed_at)
            second = int(observed_at)
            buckets = summary.setdefault("activity_buckets", [])
            bucket = next(
                (
                    item
                    for item in buckets
                    if item.get("second") == second
                    and item.get("classifier_version") == CLASSIFIER_VERSION
                ),
                None,
            )
            if bucket is None:
                bucket = {
                    "second": second,
                    "classifier_version": CLASSIFIER_VERSION,
                    **empty_classifier_counts(),
                }
                buckets.append(bucket)
            bucket[classification] += 1
            summary["activity_buckets"] = buckets[-61:]

    async def _log_query(self, row: dict[str, Any]) -> None:
        """Write one new query to Home Assistant Activity."""
        event_time = _origin_time(row)
        name, message = _query_description(row)
        query_time = (
            dt_util.as_local(event_time).isoformat(sep=" ", timespec="seconds")
            if event_time is not None
            else "no fiable"
        )
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
            aggregate_pending = getattr(self, "aggregate_pending", {})
            await self.store.async_save(
                {
                    "cursor": self.cursor,
                    "classifier_version": CLASSIFIER_VERSION,
                    "coverage": self.coverage,
                    "coverage_reason": self.coverage_reason,
                    "last_query_time": self.last_query_time,
                    "awaiting_generation": self.awaiting_generation,
                    "recovery_cursor": self.recovery_cursor,
                    "clients": self.client_data,
                    **(
                        {
                        "aggregate_pending": aggregate_pending,
                            "aggregate_pending_cursor": self.aggregate_pending_cursor,
                            "aggregate_pending_time": self.aggregate_pending_time,
                        }
                        if aggregate_pending
                        else {}
                    ),
                }
            )
