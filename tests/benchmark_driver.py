"""Run an isolated HA Core recovery against an external Pi-hole API fixture.

Start the API fixture twice: a 100-row dataset on port 8089 for warmup and the
measured dataset on port 8088. The HTTP server, dataset, and metrics file stay
outside this Home Assistant process.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import logging
import math
import os
import statistics
import tempfile
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
import psutil
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.storage import Store

from custom_components.pihole_queries.api import PiHoleClient
from custom_components.pihole_queries import const
from custom_components.pihole_queries.coordinator import PiHoleQueryCoordinator
from custom_components.pihole_queries.sensor import async_setup_entry as async_setup_sensors


PASSWORD = "synthetic-password"
SOURCE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

# Keep the same driver usable in the original baseline, which predates identity
# scoped storage and configurable retention/activity policies.
CONF_ACTIVITY_MODE = getattr(const, "CONF_ACTIVITY_MODE", "activity_mode")
CONF_HOST = const.CONF_HOST
CONF_PASSWORD = const.CONF_PASSWORD
CONF_PORT = const.CONF_PORT
CONF_RETENTION_MODE = getattr(const, "CONF_RETENTION_MODE", "retention_mode")
CONF_SCAN_INTERVAL = const.CONF_SCAN_INTERVAL
CONF_SOURCE_GENERATION = getattr(const, "CONF_SOURCE_GENERATION", "source_generation")
CONF_SOURCE_ID = getattr(const, "CONF_SOURCE_ID", "source_id")
CONF_USE_HTTPS = const.CONF_USE_HTTPS
CONF_VERIFY_SSL = const.CONF_VERIFY_SSL
DEFAULT_SCAN_INTERVAL = const.DEFAULT_SCAN_INTERVAL
DOMAIN = const.DOMAIN
LEGACY_SOURCE_GENERATION = getattr(const, "LEGACY_SOURCE_GENERATION", 0)
STORAGE_KEY_PREFIX = const.STORAGE_KEY_PREFIX
STORAGE_VERSION = const.STORAGE_VERSION


class Services:
    """Count Activity service calls at the Home Assistant service boundary."""

    def __init__(self) -> None:
        self.attempted = 0
        self.completed = 0
        self.errors = 0

    async def logbook(self, _call: ServiceCall) -> None:
        self.attempted += 1
        try:
            self.completed += 1
        except Exception:
            self.errors += 1
            raise


class ErrorCounter(logging.Handler):
    """Count error-level records while the measured Home Assistant runs."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.count = 0

    def emit(self, _record: logging.LogRecord) -> None:
        self.count += 1


def nearest_rank(values: list[float], percentile: float) -> float:
    """Return a nearest-rank percentile, preserving the observed maximum for n=1."""
    if not values:
        raise ValueError("percentile requires at least one observation")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]


def store_payload_metrics(payload: Any) -> dict[str, Any]:
    """Describe bounded numeric summaries in the serialized Store payload."""
    if not isinstance(payload, dict):
        return {"cursor": None, "client_count": 0, "activity_bucket_rows": 0,
                "query_time_bucket_rows": 0, "aggregate_pending_rows": 0}
    clients = payload.get("clients", {})
    if not isinstance(clients, dict):
        clients = {}
    activity_bucket_rows = sum(
        len(summary.get("activity_buckets", []))
        for summary in clients.values()
        if isinstance(summary, dict)
        and isinstance(summary.get("activity_buckets", []), list)
    )
    query_time_bucket_rows = sum(
        len(summary.get("query_time_buckets", []))
        for summary in clients.values()
        if isinstance(summary, dict)
        and isinstance(summary.get("query_time_buckets", []), list)
    )
    stored_query_count_total = sum(
        int(summary.get("query_count", 0))
        for summary in clients.values()
        if isinstance(summary, dict)
    )
    aggregate_pending = payload.get("aggregate_pending", {})
    forbidden_fields = {
        "domain", "last_query", "last_query_id", "recent_queries", "recent_activity",
        "query_id", "query_ids",
    }
    found_forbidden: set[str] = set()

    def inspect_fields(value: Any) -> None:
        if isinstance(value, dict):
            found_forbidden.update(str(key) for key in value if key in forbidden_fields)
            for child in value.values():
                inspect_fields(child)
        elif isinstance(value, list):
            for child in value:
                inspect_fields(child)

    inspect_fields(payload)
    return {
        "cursor": payload.get("cursor"),
        "client_count": len(clients),
        "activity_bucket_rows": activity_bucket_rows,
        "query_time_bucket_rows": query_time_bucket_rows,
        "stored_query_count_total": stored_query_count_total,
        "aggregate_pending_rows": len(aggregate_pending)
        if isinstance(aggregate_pending, (dict, list)) else 0,
        "query_detail_fields_found": sorted(found_forbidden),
    }


def instrument_store_saves(store: Any) -> dict[str, Any]:
    """Count Store save calls and payload sizes without changing their contents."""
    original_save = store.async_save
    metrics: dict[str, Any] = {
        "attempted": 0,
        "completed": 0,
        "errors": 0,
    }

    async def measured_save(payload: Any) -> None:
        metrics["attempted"] += 1
        try:
            await original_save(payload)
        except Exception:
            metrics["errors"] += 1
            raise
        metrics["completed"] += 1

    store.async_save = measured_save
    return metrics


def make_entry(entry_id: str, port: int, mode: str) -> SimpleNamespace:
    entry = SimpleNamespace(
        entry_id=entry_id,
        unique_id=f"http://pihole.example.test:{port}",
        title="Pi-hole synthetic benchmark",
        version=8,
        minor_version=1,
        data={
            CONF_SOURCE_ID: SOURCE_ID,
            CONF_SOURCE_GENERATION: LEGACY_SOURCE_GENERATION,
            CONF_HOST: "127.0.0.1",
            CONF_PORT: port,
            CONF_PASSWORD: PASSWORD,
            CONF_USE_HTTPS: False,
            CONF_VERIFY_SSL: True,
            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
        },
        options={
            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            CONF_RETENTION_MODE: mode,
            CONF_ACTIVITY_MODE: "all",
        },
        pref_disable_polling=True,
        unload_callbacks=[],
    )
    entry.async_on_unload = lambda callback: entry.unload_callbacks.append(callback)
    return entry


def make_store(hass: HomeAssistant, entry: Any, mode: str) -> Any:
    key = f"{STORAGE_KEY_PREFIX}_{entry.entry_id}"
    module = __import__("custom_components.pihole_queries", fromlist=["_IdentityStore"])
    identity_store = getattr(module, "_IdentityStore", None)
    if identity_store is None:
        store = Store(hass, STORAGE_VERSION, key)
        initial = {"cursor": 0, "clients": {}}
    else:
        current = Store(
            hass,
            STORAGE_VERSION,
            module._identity_storage_key(SOURCE_ID, LEGACY_SOURCE_GENERATION),
        )
        legacy = Store(hass, 1, key)
        store = identity_store(
            current,
            legacy,
            SOURCE_ID,
            LEGACY_SOURCE_GENERATION,
        )
        initial = {
            "cursor": 0,
            "coverage": "complete",
            "coverage_reason": None,
            "last_query_time": 1_735_689_600,
            "awaiting_generation": False,
            "clients": {},
        }
    return store, initial


async def run_one(
    hass: HomeAssistant,
    services: Services,
    *,
    entry_id: str,
    port: int,
    mode: str,
    seed_store: bool,
) -> tuple[PiHoleQueryCoordinator, PiHoleClient, Any]:
    entry = make_entry(entry_id, port, mode)
    store, initial = make_store(hass, entry, mode)
    if seed_store:
        await store.async_save(initial)
    session = aiohttp.ClientSession()
    client = PiHoleClient(session, entry.data)
    coordinator = PiHoleQueryCoordinator(hass, entry, client, store)
    await coordinator.async_load_cursor()
    return coordinator, client, store


async def run(args: argparse.Namespace) -> dict[str, Any]:
    config_dir = Path(tempfile.mkdtemp(prefix="ha-load-test-"))
    hass = HomeAssistant(str(config_dir))
    await hass.async_start()
    services = Services()
    hass.services.async_register("logbook", "log", services.logbook)

    # Three independent warmup coordinators use the separate 100-row API and
    # throw away their Stores before recording the measured RSS baseline.
    warmup_durations: list[float] = []
    for repeat in range(3):
        coordinator, client, _store = await run_one(
            hass,
            services,
            entry_id=f"synthetic-warmup-{repeat}",
            port=args.warmup_port,
            mode=args.mode,
            seed_store=True,
        )
        started = time.monotonic()
        await coordinator._async_update_data()
        warmup_durations.append(time.monotonic() - started)
        await coordinator.async_shutdown()
        await client.async_close()
        await client._session.close()
    services.attempted = 0
    services.completed = 0
    services.errors = 0
    gc.collect()

    coordinator, client, store = await run_one(
        hass,
        services,
        entry_id="synthetic-measured-run",
        port=args.port,
        mode=args.mode,
        seed_store=True,
    )
    process = psutil.Process(os.getpid())
    baseline_rss = process.memory_info().rss
    baseline_rss_sampled_monotonic = time.monotonic()
    store_saves = instrument_store_saves(store)
    ha_errors = ErrorCounter()
    logging.getLogger().addHandler(ha_errors)
    # ServiceRegistry uses a read-only slotted instance method. Count calls at
    # the registered synthetic Logbook handler boundary, which observes each
    # actual invocation and its completion/error without patching HA internals.
    logbook_calls = services
    request_latencies: list[float] = []
    request_finished_monotonic: list[float] = []
    original_get_queries = client.async_get_queries

    async def measure_request(*, length: int, cursor: int | None = None) -> dict[str, Any]:
        request_started = time.monotonic()
        try:
            response = await original_get_queries(length=length, cursor=cursor)
            request_finished_monotonic.append(time.monotonic())
            return response
        finally:
            request_latencies.append((time.monotonic() - request_started) * 1000)

    client.async_get_queries = measure_request
    result: dict[str, Any] = {}
    cycle_latencies_ms: list[float] = []
    cycle_started_monotonic: list[float] = []
    cycle_finished_monotonic: list[float] = []
    update_errors: list[str] = []
    stable_poll_intervals: list[float] = []
    feed_started: float | None = None
    feed_finished: float | None = None
    measured_started = time.monotonic()

    async def update_once() -> bool:
        """Time one actual _async_update_data invocation, excluding scheduler wait."""
        cycle_started = time.perf_counter()
        cycle_started_monotonic.append(time.monotonic())
        try:
            result.clear()
            result.update(await coordinator._async_update_data())
            return True
        except Exception as err:
            update_errors.append(f"{type(err).__name__}: {err}")
            return False
        finally:
            cycle_latencies_ms.append((time.perf_counter() - cycle_started) * 1000)
            cycle_finished_monotonic.append(time.monotonic())

    update_ok = True
    if args.scenario == "stable":
        request = urllib.request.Request(
            f"http://127.0.0.1:{args.port}/__feed/start", data=b"", method="POST"
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            feed_started = float(json.load(response)["feed_started_monotonic"])
        measured_started = feed_started
        feed_finished = feed_started + args.feed_window_seconds
        last_cycle_started: float | None = None
        while True:
            target = (
                feed_started
                if last_cycle_started is None
                else last_cycle_started + args.scan_interval_seconds
            )
            await asyncio.sleep(max(0.0, target - time.monotonic()))
            cycle_start = time.monotonic()
            if last_cycle_started is not None:
                stable_poll_intervals.append(cycle_start - last_cycle_started)
            last_cycle_started = cycle_start
            if not await update_once():
                update_ok = False
                break
            feed_ended = time.monotonic() >= feed_finished
            if feed_ended and coordinator.cursor == args.rows and getattr(
                coordinator, "coverage", "complete"
            ) == "complete":
                break
    else:
        while True:
            if not await update_once():
                update_ok = False
                break
            coverage = getattr(coordinator, "coverage", "complete")
            empty_confirmed = (
                args.scenario == "empty"
                and args.rows == 0
                and coverage == "unknown"
                and getattr(coordinator, "coverage_reason", None) == "empty_history"
            )
            if coordinator.cursor == args.rows and (coverage == "complete" or empty_confirmed):
                break
            if len(cycle_latencies_ms) > args.rows + 2:
                update_errors.append(
                    f"recovery did not finish: cursor={coordinator.cursor}, "
                    f"coverage={getattr(coordinator, 'coverage', 'complete')}"
                )
                update_ok = False
                break
    measurement_finished = time.monotonic()
    measured_finished = measurement_finished
    final_rss = process.memory_info().rss
    final_rss_sampled_monotonic = time.monotonic()
    logging.getLogger().removeHandler(ha_errors)

    store_load_errors: list[str] = []
    try:
        store_payload = await store.async_load()
    except Exception as err:
        store_payload = None
        store_load_errors.append(f"{type(err).__name__}: {err}")
    store_bytes = (
        len(json.dumps(store_payload, sort_keys=True, separators=(",", ":")).encode())
        if store_payload is not None else None
    )
    query_count = sum(summary.get("query_count", 0) for summary in result.values())
    entities: list[Any] = []
    entry = make_entry("synthetic-measured-run", args.port, args.mode)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"coordinator": coordinator}
    await async_setup_sensors(hass, entry, entities.extend)
    descriptions = __import__("custom_components.pihole_queries.sensor", fromlist=["SENSOR_DESCRIPTIONS"])
    expected_entities = (
        len(result) * len(descriptions.SENSOR_DESCRIPTIONS)
        + len(getattr(descriptions, "DIAGNOSTIC_DESCRIPTIONS", ()))
    )
    expected_events = args.rows if args.mode == "detail" else 0
    configured_clients = (
        args.configured_clients if args.configured_clients is not None else args.clients
    )
    failures: list[str] = []
    for label, actual, expected in (
        ("processed_query_ids", query_count, args.rows),
        ("observed_clients", len(result), args.clients),
        ("cursor", coordinator.cursor, args.rows),
        ("entities", len(entities), expected_entities),
        ("logbook_calls_completed", logbook_calls.completed, expected_events),
    ):
        if actual != expected:
            failures.append(f"{label}: observed {actual!r}, expected {expected!r}")
    if not update_ok:
        failures.extend(update_errors)
    failures.extend(store_load_errors)
    if args.mode == "aggregate":
        forbidden = store_payload_metrics(store_payload)["aggregate_forbidden_fields_found"]
        if forbidden:
            failures.append(f"aggregate Store contains forbidden fields: {forbidden}")
    first_page_received = (
        request_finished_monotonic[0] if request_finished_monotonic else None
    )
    cursor_confirmed = cycle_finished_monotonic[-1] if cycle_finished_monotonic else None
    observed_class_counts: dict[str, int] | None = {
        "allowed": 0, "blocked": 0, "provisional": 0
    }
    saw_classifier_counts = False
    observed_class_counts_by_client: dict[str, Any] = {}
    for summary in result.values():
        class_epochs = summary.get("classifier_counts")
        if isinstance(class_epochs, dict) and class_epochs:
            saw_classifier_counts = True
            observed_class_counts_by_client[str(len(observed_class_counts_by_client))] = class_epochs
            for epoch in class_epochs.values():
                if isinstance(epoch, dict):
                    for name in observed_class_counts:
                        observed_class_counts[name] += int(epoch.get(name, 0))
    if not saw_classifier_counts:
        observed_class_counts = None
        if not result:
            observed_class_counts = {"allowed": 0, "blocked": 0, "provisional": 0}
    class_mismatches: dict[str, int] | None = None
    if observed_class_counts is not None and args.expected_class_counts is not None:
        class_mismatches = {
            name: observed_class_counts.get(name, 0) - int(args.expected_class_counts.get(name, 0))
            for name in ("allowed", "blocked", "provisional")
        }
        if any(class_mismatches.values()):
            failures.append(f"classification count mismatch: {class_mismatches}")
    elapsed = (
        cursor_confirmed - first_page_received
        if first_page_received is not None and cursor_confirmed is not None else None
    )
    output = {
        "rows": args.rows,
        "rows_input": args.rows,
        "clients": len(result),
        "configured_clients": configured_clients,
        "observed_clients": len(result),
        "mode": args.mode,
        "scenario": args.scenario,
        "duration_seconds": elapsed,
        "measurement_window_seconds": measured_finished - measured_started,
        "measurement_pid": os.getpid(),
        "measurement_started_monotonic": measured_started,
        "measurement_finished_monotonic": measurement_finished,
        "recovery_cycles": len(cycle_latencies_ms),
        "cycle_count": len(cycle_latencies_ms),
        "cycle_latency_ms_raw": cycle_latencies_ms,
        "cycle_latency_ms_p50": statistics.median(cycle_latencies_ms) if cycle_latencies_ms else None,
        "cycle_latency_ms_p95": nearest_rank(cycle_latencies_ms, 0.95) if cycle_latencies_ms else None,
        "cycle_latency_ms_p99": nearest_rank(cycle_latencies_ms, 0.99) if cycle_latencies_ms else None,
        "cycle_percentile_method": "nearest-rank; p50 median; p95/p99 rank=ceil(n*p)",
        "cycle_count_note": "p99 equals observed maximum when n=1" if len(cycle_latencies_ms) == 1 else None,
        "cycle_started_monotonic": cycle_started_monotonic,
        "cycle_finished_monotonic": cycle_finished_monotonic,
        "request_count": len(request_latencies),
        "request_latency_ms_p50": statistics.median(request_latencies),
        "request_latency_ms_p95": nearest_rank(request_latencies, 0.95),
        "request_latency_ms_p99": nearest_rank(request_latencies, 0.99),
        "rows_processed": query_count,
        "expected_class_counts": args.expected_class_counts,
        "observed_class_counts": observed_class_counts,
        "observed_class_counts_by_client": observed_class_counts_by_client,
        "classification_count_mismatches": class_mismatches,
        "warmup_durations_seconds": warmup_durations,
        "rss_baseline_bytes": baseline_rss,
        "rss_baseline_sampled_monotonic": baseline_rss_sampled_monotonic,
        "rss_final_observed_bytes": final_rss,
        "rss_final_sampled_monotonic": final_rss_sampled_monotonic,
        "store_bytes": store_bytes,
        "store_save_attempted": store_saves["attempted"],
        "store_save_completed": store_saves["completed"],
        "store_save_errors": store_saves["errors"],
        "store_final_payload_metrics": store_payload_metrics(store_payload),
        "store_persisted_cursor": store_payload_metrics(store_payload)["cursor"],
        "logbook_calls_attempted": logbook_calls.attempted,
        "logbook_calls_completed": logbook_calls.completed,
        "logbook_call_errors": logbook_calls.errors,
        "events": logbook_calls.completed,
        "ha_error_log_records": ha_errors.count,
        "update_errors": update_errors,
        "store_load_errors": store_load_errors,
        "sensor_objects": len(entities),
        "expected_entities": expected_entities,
        "query_count": query_count,
        "cursor": coordinator.cursor,
        "coverage": getattr(coordinator, "coverage", "not-tracked-by-baseline"),
        "coverage_reason": getattr(coordinator, "coverage_reason", None),
        "event_boundary": "HA ServiceRegistry with a synthetic Logbook handler",
        "store_boundary": "Home Assistant Store on a fresh temporary config",
        "api_boundary": "Pi-hole HTTP client to an out-of-process loopback fixture",
        "first_page_received_monotonic": first_page_received,
        "final_cursor_confirmed_monotonic": cursor_confirmed,
        "stable_feed_started_monotonic": feed_started,
        "stable_feed_finished_monotonic": feed_finished,
        "stable_feed_window_seconds": args.feed_window_seconds if args.scenario == "stable" else None,
        "stable_time_to_first_page_seconds": (
            first_page_received - feed_started
            if feed_started is not None and first_page_received is not None else None
        ),
        "stable_drain_after_feed_seconds": (
            max(0.0, cursor_confirmed - feed_finished)
            if feed_finished is not None and cursor_confirmed is not None else None
        ),
        "stable_poll_interval_target_seconds": args.scan_interval_seconds if args.scenario == "stable" else None,
        "stable_poll_intervals_seconds": stable_poll_intervals,
        "update_ok": update_ok,
        "assertion_failures": failures,
        "success": not failures and store_saves["errors"] == 0
        and logbook_calls.errors == 0 and ha_errors.count == 0,
    }

    await coordinator.async_shutdown()
    await client.async_close()
    await client._session.close()
    await hass.async_stop()
    print(json.dumps(output, sort_keys=True))
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--clients", type=int, required=True)
    parser.add_argument("--mode", choices=("detail", "aggregate"), default="detail")
    parser.add_argument(
        "--scenario", choices=("recovery", "stable", "provisional", "empty"),
        default="recovery",
    )
    parser.add_argument("--configured-clients", type=int)
    parser.add_argument("--expected-class-counts", type=json.loads)
    parser.add_argument("--feed-window-seconds", type=float, default=600.0)
    parser.add_argument("--scan-interval-seconds", type=float, default=DEFAULT_SCAN_INTERVAL)
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--warmup-port", type=int, default=8089)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
