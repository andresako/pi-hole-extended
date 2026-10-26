"""Pi-hole v6 DNS query history integration."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration
from homeassistant.util import slugify

from .api import PiHoleClient
from .const import (
    CARD_FILENAME,
    CARD_URL_BASE,
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    DOMAIN,
    LEGACY_SOURCE_GENERATION,
    LEGACY_STORAGE_VERSION,
    PLATFORMS,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)
from .coordinator import PiHoleQueryCoordinator
from .websocket import async_register as websocket_api_register

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _valid_classifier_epoch(epoch: Any) -> bool:
    """Accept only canonical, bounded numeric classifier versions."""
    if (
        not isinstance(epoch, str)
        or not 1 <= len(epoch) <= 10
        or not epoch.isascii()
        or not epoch.isdecimal()
    ):
        return False
    version = int(epoch)
    return 1 <= version <= 2**31 - 1 and str(version) == epoch


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
            "baseline_unverified",
            "batch_interrupted",
            "cursor_not_reached",
            "empty_history",
            "invalid_client_summaries",
            "invalid_legacy_cursor",
            "invalid_legacy_store",
            "invalid_query_ids",
            "invalid_store_state",
            "legacy_cursor",
            "missing_client_summaries",
            "no_baseline",
            "pagination_stalled",
            "query_id_reset",
            "retention_truncation",
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

        def valid_query_time_buckets(value: Any) -> bool:
            if not isinstance(value, list) or len(value) > 61:
                return False
            seen: set[int] = set()
            for bucket in value:
                if (
                    not isinstance(bucket, dict)
                    or set(bucket) != {"second", "count"}
                    or isinstance(bucket.get("second"), bool)
                    or not isinstance(bucket.get("second"), int)
                    or bucket["second"] < 0
                    or isinstance(bucket.get("count"), bool)
                    or not isinstance(bucket.get("count"), int)
                    or bucket["count"] < 0
                    or bucket["second"] in seen
                ):
                    return False
                seen.add(bucket["second"])
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
            data["coverage_reason"] = "invalid_store_state"
            changed = True
        return changed

    async def _sanitize_legacy_backup(self) -> None:
        """Remove obsolete query rows from the v1 backup."""
        legacy = await self.legacy.async_load()
        if legacy is not None and not isinstance(legacy, dict):
            # The root cannot be inspected safely. Replace it with a small
            # diagnostic marker so opaque query payloads are not retained.
            await self.legacy.async_save(
                {"coverage": "unknown", "coverage_reason": "invalid_legacy_store"}
            )
            return
        if self._sanitize_store_data(legacy):
            await self.legacy.async_save(legacy)

    @staticmethod
    def _prepare_legacy_data(value: Any, *, invalid_reason: str = "invalid_legacy_store") -> Any:
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

        def valid_query_time_buckets(raw: Any) -> bool:
            if not isinstance(raw, list) or len(raw) > 61:
                return False
            seen: set[int] = set()
            for bucket in raw:
                if (
                    not isinstance(bucket, dict)
                    or set(bucket) != {"second", "count"}
                    or isinstance(bucket.get("second"), bool)
                    or not isinstance(bucket.get("second"), int)
                    or bucket["second"] < 0
                    or isinstance(bucket.get("count"), bool)
                    or not isinstance(bucket.get("count"), int)
                    or bucket["count"] < 0
                    or bucket["second"] in seen
                ):
                    return False
                seen.add(bucket["second"])
            return True

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
                current["data"], invalid_reason="invalid_store_state"
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


def _valid_uuid(value: Any) -> str | None:
    """Return a canonical UUID string, if the stored value is valid."""
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _entity_unique_prefix(entry: ConfigEntry) -> str:
    """Keep first-generation unique IDs and scope replacement generations."""
    generation = entry.data.get(CONF_SOURCE_GENERATION, LEGACY_SOURCE_GENERATION)
    if generation == LEGACY_SOURCE_GENERATION:
        return entry.unique_id or entry.entry_id
    return f"{entry.data[CONF_SOURCE_ID]}_{generation}"


def _archive_legacy_class_sensors(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Disable retired legacy class counters for this source generation."""
    prefix = f"{_entity_unique_prefix(entry)}_"
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity.platform != DOMAIN
            or entity.domain != "sensor"
            or not entity.unique_id.startswith(prefix)
            or not _is_retired_legacy_class_sensor(entity)
            or entity.disabled_by is not None
        ):
            continue
        registry.async_update_entity(
            entity.entity_id,
            disabled_by=RegistryEntryDisabler.INTEGRATION,
        )


def _is_retired_legacy_class_sensor(entity: er.RegistryEntry) -> bool:
    """Match only the two known 0.6 class descriptors, never other entities."""
    legacy_classes = (
        entity.unique_id.endswith("_allowed")
        and (
            entity.translation_key == "legacy_allowed"
            or (
                entity.translation_key is None
                and entity.original_name == "Permitidas"
            )
        )
    ) or (
        entity.unique_id.endswith("_blocked")
        and (
            entity.translation_key == "legacy_blocked"
            or (
                entity.translation_key is None
                and entity.original_name == "Bloqueadas"
            )
        )
    )
    legacy_class_windows = (
        entity.unique_id.endswith("_allowed_last_minute")
        and (
            entity.translation_key == "allowed_last_minute"
            or (
                entity.translation_key is None
                and entity.original_name == "Permitidas (último minuto)"
            )
        )
    ) or (
        entity.unique_id.endswith("_blocked_last_minute")
        and (
            entity.translation_key == "blocked_last_minute"
            or (
                entity.translation_key is None
                and entity.original_name == "Bloqueadas (último minuto)"
            )
        )
    )
    return legacy_classes or legacy_class_windows


def _identity_storage_key(source_id: str, generation: str) -> str:
    """Build a Store key scoped to one Pi-hole source generation."""
    return f"{STORAGE_KEY_PREFIX}_{source_id}_{generation}"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration domain."""
    hass.data.setdefault(DOMAIN, {})
    websocket_api_register(hass)
    await _async_register_frontend(hass)
    return True


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the activity card and load it in every dashboard."""
    if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
        return
    version = (await async_get_integration(hass, DOMAIN)).version
    # Serve the whole folder: the card script plus its translations/*.json files.
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_BASE, str(Path(__file__).parent / "frontend"), False)]
    )
    url = f"{CARD_URL_BASE}/{CARD_FILENAME}"
    add_extra_js_url(hass, f"{url}?v={version}")


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove obsolete sensors and assign concise, integration-prefixed IDs."""
    registry = er.async_get(hass)
    metric_names = {
        "consultas_dns": "Consultas DNS",
        "permitidas": "Permitidas",
        "bloqueadas": "Bloqueadas",
        "consultas_por_segundo": "Consultas por segundo",
        "permitidas_por_segundo": "Permitidas por segundo",
        "bloqueadas_por_segundo": "Bloqueadas por segundo",
    }
    if entry.version < 2:
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.unique_id.endswith(("_last_domain", "_last_result")):
                registry.async_remove(entity.entity_id)
        hass.config_entries.async_update_entry(entry, version=2)

    if entry.version < 3:
        device_registry = dr.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain != "sensor" or not entity.device_id:
                continue
            if entity.entity_id.startswith("sensor.phe_"):
                continue
            device = device_registry.async_get(entity.device_id)
            if device is None or device.name_by_user:
                continue
            metric = next(
                (
                    name
                    for suffix, name in metric_names.items()
                    if entity.entity_id.endswith(f"_{suffix}")
                ),
                None,
            )
            if metric is None:
                continue

            # Only migrate Home Assistant's default IDs. Leave any user-customized
            # entity ID untouched.
            old_default_name = slugify(f"{device.name} {metric}")
            old_default = f"sensor.{old_default_name}"
            if entity.entity_id != old_default:
                continue

            client_name = device.name
            match = re.fullmatch(r"(.+) \(([^()]*)\)", client_name)
            if match:
                client_name, client_ip = match.groups()
                if client_ip != "Desconocida":
                    client_name = client_ip
            new_object_id = slugify(f"PHE {client_name} {metric}")
            new_entity_id = _available_entity_id(registry, f"sensor.{new_object_id}")
            registry.async_update_entity(entity.entity_id, new_entity_id=new_entity_id)

        hass.config_entries.async_update_entry(entry, version=3)

    if entry.version < 4:
        device_registry = dr.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            prefix = "sensor.pi_hole_extended_"
            if not entity.entity_id.startswith(prefix) or not entity.device_id:
                continue
            device = device_registry.async_get(entity.device_id)
            if device is None or device.name_by_user:
                continue
            metric = next(
                (
                    name
                    for suffix, name in metric_names.items()
                    if entity.entity_id.endswith(f"_{suffix}")
                ),
                None,
            )
            if metric is None:
                continue
            client_id = _client_id_from_device_name(device.name)
            old_default = f"sensor.{slugify(f'Pi-hole Extended {client_id} {metric}')}"
            if entity.entity_id != old_default:
                continue
            new_entity_id = f"sensor.phe_{entity.entity_id.removeprefix(prefix)}"
            new_entity_id = _available_entity_id(registry, new_entity_id)
            registry.async_update_entity(entity.entity_id, new_entity_id=new_entity_id)

        hass.config_entries.async_update_entry(entry, version=4)

    if entry.version < 5:
        # Preserve all registered IDs. There is no reliable marker that
        # distinguishes a former default ID from a user customization.
        hass.config_entries.async_update_entry(entry, version=5)

    if entry.version < 6:
        # The one-second rate sensors are replaced by rolling 60-second
        # counts. Remove only those obsolete registry entries; cumulative
        # query counters and the Recorder database are kept intact.
        old_rate_suffixes = ("_query_rate", "_allowed_rate", "_blocked_rate")
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain == "sensor" and entity.unique_id.endswith(old_rate_suffixes):
                registry.async_remove(entity.entity_id)
        hass.config_entries.async_update_entry(entry, version=6)

    if entry.version < 7:
        # Keep the Home Assistant registry authoritative for existing IDs.
        hass.config_entries.async_update_entry(entry, version=7)
    if entry.version < 8:
        data = dict(entry.data)
        data[CONF_SOURCE_ID] = _valid_uuid(data.get(CONF_SOURCE_ID)) or str(uuid4())
        generation = data.get(CONF_SOURCE_GENERATION)
        data[CONF_SOURCE_GENERATION] = (
            generation
            if generation == LEGACY_SOURCE_GENERATION or _valid_uuid(generation)
            else LEGACY_SOURCE_GENERATION
        )
        hass.config_entries.async_update_entry(entry, data=data, version=8)
    return entry.version == 8


def _client_id_from_device_name(device_name: str) -> str:
    """Use the client IP in entity IDs when it is part of the device name."""
    match = re.fullmatch(r"(.+) \(([^()]*)\)", device_name)
    if match:
        client_name, client_ip = match.groups()
        if client_ip != "Desconocida":
            return client_ip
        return client_name
    return device_name


def _available_entity_id(registry: er.EntityRegistry, entity_id: str) -> str:
    """Return an unused entity ID, adding a numeric suffix if necessary."""
    base_entity_id = entity_id
    collision = 2
    while registry.async_get(entity_id) is not None:
        entity_id = f"{base_entity_id}_{collision}"
        collision += 1
    return entity_id


def _canonical_entity_id(entry: ConfigEntry, entity: er.RegistryEntry) -> str | None:
    """Build the stable recorder-friendly ID for a Pi-hole client metric."""
    return canonical_entity_id(entry, entity.domain, entity.unique_id)


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
    unique_prefix = f"{_entity_unique_prefix(entry)}_"
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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a configured Pi-hole and begin tracking new DNS queries."""
    _archive_legacy_class_sensors(hass, entry)
    client = PiHoleClient(async_get_clientsession(hass), {**entry.data, **entry.options})
    source_id = _valid_uuid(entry.data.get(CONF_SOURCE_ID))
    generation_value = entry.data.get(CONF_SOURCE_GENERATION)
    generation = (
        generation_value
        if generation_value == LEGACY_SOURCE_GENERATION
        else _valid_uuid(generation_value)
    )
    if source_id is None or generation is None:
        await client.async_close()
        raise HomeAssistantError("La entrada no contiene identidad source_id/source_generation válida")
    store = _IdentityStore(
        Store(
            hass,
            STORAGE_VERSION,
            _identity_storage_key(source_id, generation),
        ),
        Store(
            hass,
            LEGACY_STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}_{entry.entry_id}",
        ),
        source_id,
        generation,
    )
    coordinator = PiHoleQueryCoordinator(hass, entry, client, store)
    try:
        await coordinator.async_load_cursor()
        await coordinator.async_config_entry_first_refresh()
        hass.data[DOMAIN][entry.entry_id] = {"client": client, "coordinator": coordinator}

        registry = er.async_get(hass)

        @callback
        def normalize_entity(event: Any) -> None:
            """Keep IDs under phe_ when a new Pi-hole client is discovered."""
            if event.data.get("action") != "create":
                return
            entity = registry.async_get(event.data.get("entity_id"))
            if (
                entity is None
                or entity.config_entry_id != entry.entry_id
                or entity.platform != DOMAIN
            ):
                return
            target = _canonical_entity_id(entry, entity)
            if target and entity.entity_id != target:
                registry.async_update_entity(
                    entity.entity_id, new_entity_id=_available_entity_id(registry, target)
                )

        entry.async_on_unload(
            hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, normalize_entity)
        )
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry.async_on_unload(entry.add_update_listener(_async_options_updated))
        return True
    except BaseException:
        # A failed first refresh or platform setup must not strand a Pi-hole SID.
        hass.data[DOMAIN].pop(entry.entry_id, None)
        try:
            await coordinator.async_shutdown()
        finally:
            await client.async_close()
        raise


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload so a changed polling interval takes effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop polling and close the Pi-hole session when unloading."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = hass.data[DOMAIN].pop(entry.entry_id)
    try:
        await runtime["coordinator"].async_shutdown()
    finally:
        await runtime["client"].async_close()
    return True
