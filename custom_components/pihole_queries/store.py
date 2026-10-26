"""Persistent, source-scoped Store for the query cursor and client summaries."""

from __future__ import annotations

import math
import re
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import (
    LEGACY_SOURCE_GENERATION,
    STORAGE_VERSION,
    CoverageReason,
)
from .validation import valid_classifier_epoch as _valid_classifier_epoch
from .validation import valid_query_time_buckets


def _valid_query_id(value: Any) -> bool:
    """Accept bounded non-negative query IDs from current and legacy Stores."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 <= value < 2**63
    if isinstance(value, float):
        return math.isfinite(value) and value.is_integer() and 0 <= value < 2**63
    if isinstance(value, str) and re.fullmatch(r"\d{1,19}", value.strip()):
        return int(value.strip()) < 2**63
    return False


def _valid_timestamp(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


class _IdentityStore:
    """Store cursor data under a stable source and generation identity."""

    def __init__(
        self,
        current: Store,
        legacy: Store,
        source_id: str,
        generation: str,
    ) -> None:
        self.current = current
        self.legacy = legacy
        self.source_id = source_id
        self.generation = generation

    @staticmethod
    def _sanitize_store_data(data: Any) -> bool:
        """Keep the cursor, client totals, and bounded numeric summaries."""
        if not isinstance(data, dict):
            return False
        root_fields = {
            "cursor",
            "classifier_version",
            "coverage",
            "coverage_reason",
            "last_query_time",
            "awaiting_generation",
            "recovery_cursor",
            "clients",
            "aggregate_pending",
            "aggregate_pending_cursor",
            "aggregate_pending_time",
        }
        changed = False
        for field in tuple(data):
            if field not in root_fields:
                data.pop(field)
                changed = True
        coverage_reasons = {
            CoverageReason.BASELINE_UNVERIFIED,
            CoverageReason.BATCH_INTERRUPTED,
            CoverageReason.CURSOR_NOT_REACHED,
            CoverageReason.EMPTY_HISTORY,
            CoverageReason.INVALID_CLIENT_SUMMARIES,
            CoverageReason.INVALID_LEGACY_CURSOR,
            CoverageReason.INVALID_LEGACY_STORE,
            CoverageReason.INVALID_QUERY_IDS,
            CoverageReason.INVALID_STORE_STATE,
            CoverageReason.LEGACY_CURSOR,
            CoverageReason.MISSING_CLIENT_SUMMARIES,
            CoverageReason.NO_BASELINE,
            CoverageReason.PAGINATION_STALLED,
            CoverageReason.QUERY_ID_RESET,
            CoverageReason.RETENTION_TRUNCATION,
        }
        for field, valid in (
            ("cursor", _valid_query_id(data.get("cursor"))),
            ("classifier_version", isinstance(data.get("classifier_version"), int)
                and not isinstance(data.get("classifier_version"), bool)
                and 1 <= data.get("classifier_version") <= 2**31 - 1),
            ("coverage", isinstance(data.get("coverage"), str)
                and data.get("coverage") in {"unknown", "complete", "partial"}),
            ("coverage_reason", data.get("coverage_reason") is None
                or (isinstance(data.get("coverage_reason"), str)
                    and data.get("coverage_reason") in coverage_reasons)),
            ("last_query_time", _valid_timestamp(data.get("last_query_time"))),
            ("awaiting_generation", isinstance(data.get("awaiting_generation"), bool)),
            ("recovery_cursor", data.get("recovery_cursor") is None
                or _valid_query_id(data.get("recovery_cursor"))),
        ):
            if field in data and not valid:
                data.pop(field)
                changed = True
        if "clients" in data and not isinstance(data["clients"], dict):
            data.pop("clients")
            changed = True
        aggregate_fields = {
            "client_name",
            "client_ip",
            "query_count",
            "allowed_count",
            "blocked_count",
            "queries_last_minute",
            "allowed_last_minute",
            "blocked_last_minute",
            "query_time_buckets",
            "classifier_version",
            "classifier_counts",
            "classifier_rates",
            "queries_observed_last_minute",
            "activity_buckets",
        }

        def valid_counts(value: Any) -> bool:
            return isinstance(value, dict) and bool(value) and all(
                _valid_classifier_epoch(epoch)
                and isinstance(counts, dict)
                and set(counts) == {"allowed", "blocked", "provisional"}
                and all(
                    isinstance(counts[name], int)
                    and not isinstance(counts[name], bool)
                    and counts[name] >= 0
                    for name in counts
                )
                for epoch, counts in value.items()
            )

        def clean_counts(value: Any) -> dict[str, dict[str, int]]:
            if not isinstance(value, dict):
                return {}
            return {
                epoch: counts
                for epoch, counts in value.items()
                if _valid_classifier_epoch(epoch)
                and isinstance(counts, dict)
                and set(counts) == {"allowed", "blocked", "provisional"}
                and all(
                    isinstance(counts[name], int)
                    and not isinstance(counts[name], bool)
                    and counts[name] >= 0
                    for name in counts
                )
            }

        def valid_buckets(value: Any) -> bool:
            if not isinstance(value, list) or len(value) > 61:
                return False
            seen: set[tuple[int, int]] = set()
            for bucket in value:
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
                ):
                    return False
                second, version = bucket["second"], bucket["classifier_version"]
                if (
                    isinstance(second, bool)
                    or not isinstance(second, int)
                    or second < 0
                    or isinstance(version, bool)
                    or not isinstance(version, int)
                    or not 1 <= version <= 2**31 - 1
                    or any(
                        isinstance(bucket[name], bool)
                        or not isinstance(bucket[name], int)
                        or bucket[name] < 0
                        for name in ("allowed", "blocked", "provisional")
                    )
                ):
                    return False
                key = (second, version)
                if key in seen:
                    return False
                seen.add(key)
            return True


        def valid_pending(value: Any) -> bool:
            if not isinstance(value, dict):
                return False
            for key, item in value.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or len(key) > 255
                    or not isinstance(item, dict)
                    or not {"client_name", "client_ip", "counts", "buckets"}.issubset(item)
                    or set(item) - {
                        "client_name", "client_ip", "counts", "buckets", "query_time_buckets"
                    }
                    or any(
                        item[name] is not None and not isinstance(item[name], str)
                        for name in ("client_name", "client_ip")
                    )
                    or not valid_counts({"1": item.get("counts")})
                    or not valid_buckets(item.get("buckets"))
                    or not valid_query_time_buckets(item.get("query_time_buckets", []))
                    or any(
                        item[name] is not None and len(item[name]) > 255
                        for name in ("client_name", "client_ip")
                    )
                ):
                    return False
            return True

        for client_key, summary in tuple(data.get("clients", {}).items()):
            if not isinstance(client_key, str) or not client_key or len(client_key) > 255:
                data["clients"].pop(client_key)
                changed = True
                continue
            if not isinstance(summary, dict):
                data["clients"].pop(client_key)
                changed = True
                continue
            for field in tuple(summary):
                if field not in aggregate_fields:
                    summary.pop(field)
                    changed = True
                    continue
                value = summary[field]
                valid = True
                if field in {"client_name", "client_ip"}:
                    valid = isinstance(value, str) and len(value) <= 255
                elif field in {
                    "query_count",
                    "allowed_count",
                    "blocked_count",
                    "queries_last_minute",
                    "allowed_last_minute",
                    "blocked_last_minute",
                    "queries_observed_last_minute",
                }:
                    valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
                elif field == "classifier_version":
                    valid = (
                        isinstance(value, int)
                        and not isinstance(value, bool)
                        and 1 <= value <= 2**31 - 1
                    )
                elif field in {"classifier_counts", "classifier_rates"}:
                    cleaned = clean_counts(value)
                    if cleaned != value:
                        if cleaned:
                            summary[field] = cleaned
                        else:
                            summary.pop(field)
                        changed = True
                    continue
                elif field == "activity_buckets":
                    valid = valid_buckets(value)
                elif field == "query_time_buckets":
                    valid = valid_query_time_buckets(value)
                if not valid:
                    summary.pop(field)
                    changed = True

        pending_fields = {
            "aggregate_pending_cursor",
            "aggregate_pending_time",
        }
        pending = data.get("aggregate_pending")
        has_pending_metadata = any(field in data for field in pending_fields)
        pending_valid = valid_pending(pending) if "aggregate_pending" in data else not has_pending_metadata
        if pending_valid and pending:
            pending_cursor = data.get("aggregate_pending_cursor")
            pending_time = data.get("aggregate_pending_time")
            confirmed_cursor = data.get("cursor")
            recovery_cursor = data.get("recovery_cursor")
            pending_valid = (
                isinstance(pending_cursor, int)
                and not isinstance(pending_cursor, bool)
                and 0 <= pending_cursor < 2**63
                and isinstance(confirmed_cursor, int)
                and not isinstance(confirmed_cursor, bool)
                and pending_cursor > confirmed_cursor
                and isinstance(recovery_cursor, int)
                and not isinstance(recovery_cursor, bool)
                and 0 <= recovery_cursor < 2**63
                and confirmed_cursor <= recovery_cursor < pending_cursor
                and _valid_timestamp(pending_time)
            )
        elif pending_valid and has_pending_metadata:
            pending_valid = False
        if not pending_valid:
            for field in ("aggregate_pending", *pending_fields, "recovery_cursor"):
                if field in data:
                    data.pop(field)
                    changed = True
            data["coverage"] = "unknown"
            data["coverage_reason"] = CoverageReason.INVALID_STORE_STATE
            changed = True
        return changed

    async def _sanitize_legacy_backup(self) -> None:
        """Remove obsolete query rows from the v1 backup."""
        legacy = await self.legacy.async_load()
        if legacy is not None and not isinstance(legacy, dict):
            # The root cannot be inspected safely. Replace it with a small
            # diagnostic marker so opaque query payloads are not retained.
            await self.legacy.async_save(
                {"coverage": "unknown", "coverage_reason": CoverageReason.INVALID_LEGACY_STORE}
            )
            return
        if self._sanitize_store_data(legacy):
            await self.legacy.async_save(legacy)

    @staticmethod
    def _prepare_legacy_data(value: Any, *, invalid_reason: str = CoverageReason.INVALID_LEGACY_STORE) -> Any:
        """Keep usable v1 aggregates while excluding malformed optional fields."""
        if not isinstance(value, dict):
            # Let the coordinator's fail-closed root-type guard record invalid
            # non-null values as `invalid_legacy_store`; None also represents
            # an absent Home Assistant Store file and cannot be distinguished.
            return value

        data = dict(value)
        malformed = False

        def valid_id(raw: Any) -> bool:
            return _valid_query_id(raw)

        def finite_number(raw: Any) -> bool:
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return False
            try:
                return math.isfinite(raw)
            except (OverflowError, TypeError, ValueError):
                return False


        def sanitize_counts(raw: Any) -> dict[str, dict[str, int]]:
            if not isinstance(raw, dict):
                return {}
            return {
                epoch: counts
                for epoch, counts in raw.items()
                if _valid_classifier_epoch(epoch)
                and isinstance(counts, dict)
                and set(counts) == {"allowed", "blocked", "provisional"}
                and all(
                    isinstance(counts[name], int)
                    and not isinstance(counts[name], bool)
                    and counts[name] >= 0
                    for name in counts
                )
            }

        if "cursor" in data and not valid_id(data["cursor"]):
            data.pop("cursor")
            malformed = True
        if "coverage" in data and (
            not isinstance(data["coverage"], str)
            or data["coverage"] not in {"unknown", "complete", "partial"}
        ):
            data.pop("coverage")
            malformed = True
        if "coverage_reason" in data and data["coverage_reason"] is not None and not isinstance(
            data["coverage_reason"], str
        ):
            data.pop("coverage_reason")
            malformed = True
        for field in ("last_query_time",):
            raw = data.get(field)
            if field in data and raw is not None and (
                not finite_number(raw)
            ):
                data.pop(field)
                malformed = True
        if "awaiting_generation" in data and not isinstance(data["awaiting_generation"], bool):
            data.pop("awaiting_generation")
            malformed = True
        if "recovery_cursor" in data and data["recovery_cursor"] is not None and not valid_id(
            data["recovery_cursor"]
        ):
            data.pop("recovery_cursor")
            malformed = True

        clients = data.get("clients")
        if "clients" in data and not isinstance(clients, dict):
            data.pop("clients")
            malformed = True
        if isinstance(clients, dict):
            clean_clients: dict[str, dict[str, Any]] = {}
            for key, raw_summary in clients.items():
                if not isinstance(raw_summary, dict):
                    malformed = True
                    continue
                summary = dict(raw_summary)
                for field in ("last_query", "last_query_id", "recent_queries", "recent_activity"):
                    summary.pop(field, None)
                count = summary.get("query_count")
                if (
                    "query_count" not in summary
                    or isinstance(count, bool)
                    or not isinstance(count, int)
                    or count < 0
                ):
                    # Keep this client visible, but
                    # never claim its imported coverage is complete.
                    summary["query_count"] = 0
                    malformed = True
                for field in ("allowed_count", "blocked_count"):
                    count = summary.get(field)
                    if field in summary and (
                        isinstance(count, bool)
                        or not isinstance(count, int)
                        or count < 0
                    ):
                        summary.pop(field)
                        malformed = True
                for field in ("classifier_counts", "classifier_rates"):
                    if field not in summary:
                        continue
                    raw_counts = summary[field]
                    clean_counts = sanitize_counts(raw_counts)
                    if clean_counts != raw_counts:
                        malformed = True
                        if clean_counts:
                            summary[field] = clean_counts
                        else:
                            summary.pop(field)
                for field in ("client_name", "client_ip"):
                    if field in summary and not isinstance(summary[field], str):
                        summary.pop(field)
                        malformed = True
                if "queries_observed_last_minute" in summary:
                    count = summary["queries_observed_last_minute"]
                    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                        summary.pop("queries_observed_last_minute")
                        malformed = True
                if "queries_last_minute" in summary:
                    count = summary["queries_last_minute"]
                    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                        summary.pop("queries_last_minute")
                        malformed = True
                if "query_time_buckets" in summary and not valid_query_time_buckets(
                    summary["query_time_buckets"]
                ):
                    summary.pop("query_time_buckets")
                    malformed = True
                clean_clients[str(key)] = summary
            data["clients"] = clean_clients

        if malformed:
            data["coverage"] = "unknown"
            data["coverage_reason"] = invalid_reason
        return data

    async def async_load(self) -> Any:
        """Load v2 data or import the v1 Store into the legacy generation once."""
        current = await self.current.async_load()
        if current is not None:
            if (
                not isinstance(current, dict)
                or isinstance(current.get("schema_version"), bool)
                or not isinstance(current.get("schema_version"), int)
                or current.get("schema_version") != STORAGE_VERSION
                or current.get("source_id") != self.source_id
                or current.get("source_generation") != self.generation
                or not isinstance(current.get("data"), dict)
            ):
                raise HomeAssistantError(
                    "El Store v2 no coincide con la identidad o el esquema de esta entrada"
                )
            prepared = self._prepare_legacy_data(
                current["data"], invalid_reason=CoverageReason.INVALID_STORE_STATE
            )
            changed = prepared != current["data"]
            changed = self._sanitize_store_data(prepared) or changed
            if changed:
                await self.current.async_save(
                    {
                        "schema_version": STORAGE_VERSION,
                        "source_id": self.source_id,
                        "source_generation": self.generation,
                        "data": prepared,
                    }
                )
            await self._sanitize_legacy_backup()
            return prepared

        if self.generation != LEGACY_SOURCE_GENERATION:
            # The v1 key is scoped only by entry_id and therefore belongs to
            # the first generation. Importing it here would carry the retired
            # Pi-hole cursor and counters into a replacement server.
            await self._sanitize_legacy_backup()
            return None

        legacy = await self.legacy.async_load()
        if legacy is None:
            return None
        prepared = self._prepare_legacy_data(legacy)
        if not isinstance(prepared, dict):
            # The coordinator marks coverage unknown and writes a valid v2 map
            # only after a fresh baseline. In aggregate mode the unusable v1
            # root must also be scrubbed so opaque data cannot retain queries.
            await self._sanitize_legacy_backup()
            return prepared
        if self._sanitize_store_data(legacy):
            await self.legacy.async_save(legacy)
        self._sanitize_store_data(prepared)
        await self.current.async_save(
            {
                "schema_version": STORAGE_VERSION,
                "source_id": self.source_id,
                "source_generation": self.generation,
                "data": prepared,
            }
        )
        return prepared

    async def async_save(self, data: Any) -> None:
        """Persist coordinator data in the source-scoped v2 envelope."""
        await self.current.async_save(
            {
                "schema_version": STORAGE_VERSION,
                "source_id": self.source_id,
                "source_generation": self.generation,
                "data": data,
            }
        )
