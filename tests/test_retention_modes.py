"""Retention behavior across Activity, entity attributes, and Store."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.pihole_queries.__init__ import _IdentityStore
from custom_components.pihole_queries.const import RETENTION_AGGREGATE
from custom_components.pihole_queries.coordinator import PiHoleQueryCoordinator
from custom_components.pihole_queries.sensor import SENSOR_DESCRIPTIONS, PiHoleClientSensor


class MemoryStore:
    def __init__(self, value: Any) -> None:
        self.value = deepcopy(value)
        self.saves: list[Any] = []
        self.save_error: Exception | None = None

    async def async_load(self) -> Any:
        return deepcopy(self.value)

    async def async_save(self, value: Any) -> None:
        if self.save_error is not None:
            raise self.save_error
        self.value = deepcopy(value)
        self.saves.append(deepcopy(value))


class StreamingApi:
    def __init__(self, responses: dict[int | None, dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[int, int | None]] = []

    async def async_get_queries(
        self, *, length: int, cursor: int | None = None
    ) -> dict[str, Any]:
        self.calls.append((length, cursor))
        return deepcopy(self.responses.get(cursor, {"queries": []}))


class Services:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def async_call(self, _domain: str, _service: str, data: dict[str, Any], **_kwargs: Any) -> None:
        self.calls.append(data)


def _row(query_id: int, status: str = "FORWARDED", domain: str | None = None) -> dict[str, Any]:
    return {
        "id": query_id,
        "time": 1_735_689_600 + query_id,
        "domain": domain or f"secret-{query_id}.example.test",
        "status": status,
        "client": {"ip": "192.0.2.10", "name": "Test client"},
    }


def _summary() -> dict[str, Any]:
    return {
        "client_name": "Test client",
        "client_ip": "192.0.2.10",
        "query_count": 5,
        "allowed_count": 4,
        "blocked_count": 1,
        "classifier_version": 1,
        "classifier_counts": {"1": {"allowed": 4, "blocked": 1, "provisional": 0}},
        "classifier_rates": {"1": {"allowed": 0, "blocked": 0, "provisional": 0}},
        "queries_observed_last_minute": 0,
        "last_query_id": 100,
        "last_query": {"id": 100, "domain": "old.example.test"},
        "recent_queries": [{"id": 100, "domain": "old.example.test"}],
        "recent_activity": [{"observed_at": 1_735_689_600, "classifier_version": 1, "classification": "allowed"}],
    }


def _aggregate_coordinator(
    responses: dict[int | None, dict[str, Any]], *, pending: dict[str, Any] | None = None,
    recovery_cursor: int | None = None,
) -> PiHoleQueryCoordinator:
    coordinator = object.__new__(PiHoleQueryCoordinator)
    coordinator.client = StreamingApi(responses)
    coordinator.store = MemoryStore({"cursor": 100, "clients": {"192.0.2.10": _summary()}})
    coordinator.hass = SimpleNamespace(services=Services())
    coordinator.cursor = 100
    coordinator.client_data = {"192.0.2.10": _summary()}
    coordinator._bootstrapped = True
    coordinator.coverage = "complete"
    coordinator.coverage_reason = None
    coordinator.last_query_time = 1_735_689_600
    coordinator.awaiting_generation = False
    coordinator.recovery_cursor = recovery_cursor
    coordinator._client_store_invalid = False
    coordinator._store_state_invalid = False
    coordinator.retention_mode = RETENTION_AGGREGATE
    coordinator.activity_mode = "all"
    coordinator.aggregate_pending = deepcopy(pending or {})
    coordinator.aggregate_pending_cursor = None
    coordinator.aggregate_pending_time = None
    return coordinator


@pytest.mark.asyncio
async def test_aggregate_streams_rows_and_keeps_no_query_details() -> None:
    coordinator = _aggregate_coordinator(
        {
            None: {"queries": [_row(103, "GRAVITY")]},
            102: {"queries": [_row(102, "FORWARDED")]},
            101: {"queries": [_row(101, "UNKNOWN")]},
            100: {"queries": [_row(100)]},
        }
    )

    data = await coordinator._async_update_data()
    summary = data["192.0.2.10"]
    saved = coordinator.store.value

    assert summary["query_count"] == 8
    assert summary["classifier_counts"]["1"] == {
        "allowed": 5,
        "blocked": 2,
        "provisional": 1,
    }
    assert coordinator.cursor == 103
    assert coordinator.hass.services.calls == []
    assert coordinator.client.calls == [(1, None), (1, 102), (1, 101), (1, 100)]
    assert not {"last_query", "last_query_id", "recent_queries", "recent_activity"} & summary.keys()
    assert "secret-103.example.test" not in repr(saved)
    assert "secret-102.example.test" not in repr(saved)
    assert "id" not in repr(summary.get("activity_buckets", []))


@pytest.mark.asyncio
async def test_incomplete_aggregate_recovery_persists_only_numeric_pending_totals() -> None:
    coordinator = _aggregate_coordinator(
        {
            None: {"queries": [_row(103, "GRAVITY")]},
            102: {"queries": [_row(102, "FORWARDED")]},
            101: {"queries": []},
        }
    )

    await coordinator._async_update_data()
    first_store = coordinator.store.value
    assert coordinator.cursor == 100
    assert first_store["aggregate_pending_cursor"] == 103
    assert first_store["recovery_cursor"] == 101
    assert first_store["aggregate_pending"]["192.0.2.10"]["counts"] == {
        "allowed": 1,
        "blocked": 1,
        "provisional": 0,
    }
    assert "secret-103.example.test" not in repr(first_store)
    assert coordinator.client_data["192.0.2.10"]["query_count"] == 5

    coordinator.client.responses.update(
        {
            None: {"queries": [_row(104, "UNKNOWN")]},
            101: {"queries": [_row(101, "UNKNOWN")]},
            100: {"queries": [_row(100)]},
        }
    )
    await coordinator._async_update_data()

    summary = coordinator.client_data["192.0.2.10"]
    assert coordinator.cursor == 103
    assert summary["query_count"] == 8
    assert summary["classifier_counts"]["1"] == {
        "allowed": 5,
        "blocked": 2,
        "provisional": 1,
    }
    assert coordinator.hass.services.calls == []


@pytest.mark.asyncio
async def test_recovery_merges_overlapping_activity_buckets_before_store_scrub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "custom_components.pihole_queries.coordinator.time.time",
        lambda: 1_735_689_600 + 150,
    )
    coordinator = _aggregate_coordinator(
        {
            None: {"queries": [_row(103, "FORWARDED")]},
            102: {"queries": [_row(102, "FORWARDED")]},
            101: {"queries": []},
        }
    )
    same_second = 1_735_689_600 + 150
    initial_bucket = {
        "second": same_second,
        "classifier_version": 1,
        "allowed": 4,
        "blocked": 0,
        "provisional": 0,
    }
    coordinator.client_data["192.0.2.10"]["activity_buckets"] = [initial_bucket]

    await coordinator._async_update_data()
    coordinator.client.responses.update(
        {
            None: {"queries": [_row(104, "UNKNOWN")]},
            101: {"queries": [_row(101, "FORWARDED")]},
            100: {"queries": [_row(100)]},
        }
    )
    await coordinator._async_update_data()

    buckets = coordinator.client_data["192.0.2.10"]["activity_buckets"]
    merged = [bucket for bucket in buckets if bucket["second"] == same_second]
    assert merged == [
        {
            "second": same_second,
            "classifier_version": 1,
            "allowed": 7,
            "blocked": 0,
            "provisional": 0,
        }
    ]

    current = MemoryStore(None)
    legacy = MemoryStore(coordinator.store.value)
    store = _IdentityStore(
        current,
        legacy,
        "5be6fe2a-4c73-4798-ae2e-4f1de7de34cd",
        "legacy",
    )
    loaded = await store.async_load()
    assert loaded["clients"]["192.0.2.10"]["activity_buckets"] == buckets


@pytest.mark.asyncio
async def test_truncated_aggregate_recovery_resumes_batches_and_keeps_latest_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "custom_components.pihole_queries.coordinator.QUERY_BATCH_SIZE", 2
    )
    base = 1_735_689_600
    rows = {query_id: _row(query_id, "FORWARDED") for query_id in range(101, 105)}
    rows[103]["time"] = base + 20  # Older ID responses must not replace the high-water time.
    api_responses = {
        None: {"queries": [rows[104]], "earliest_timestamp": base + 100},
        103: {"queries": [rows[103]]},
        102: {"queries": [rows[102]]},
        101: {"queries": [rows[101]]},
        100: {"queries": []},
    }
    coordinator = _aggregate_coordinator(api_responses)
    coordinator.last_query_time = base + 99

    await coordinator._async_update_data()

    assert coordinator.cursor == 100
    assert coordinator.recovery_cursor == 102
    assert coordinator.aggregate_pending_cursor == 104
    assert coordinator.aggregate_pending["192.0.2.10"]["counts"]["allowed"] == 2
    assert coordinator.aggregate_pending_time == base + 104
    assert coordinator.last_query_time == base + 99
    assert coordinator.coverage_reason == "retention_truncation"

    await coordinator._async_update_data()

    assert coordinator.cursor == 100
    assert coordinator.recovery_cursor == 100
    assert coordinator.aggregate_pending["192.0.2.10"]["counts"]["allowed"] == 4
    assert coordinator.aggregate_pending_time == base + 104
    assert coordinator.last_query_time == base + 99

    await coordinator._async_update_data()

    summary = coordinator.client_data["192.0.2.10"]
    assert coordinator.cursor == 104
    assert summary["query_count"] == 9
    assert coordinator.last_query_time == base + 104
    assert coordinator.aggregate_pending == {}
    assert coordinator.recovery_cursor is None
    assert coordinator.coverage == "partial"
    assert coordinator.coverage_reason == "retention_truncation"


@pytest.mark.asyncio
async def test_aggregate_pending_survives_restart_and_resumes_at_saved_page() -> None:
    first = _aggregate_coordinator(
        {
            None: {"queries": [_row(103, "GRAVITY")]},
            102: {"queries": [_row(102, "FORWARDED")]},
            101: {"queries": []},
        }
    )
    await first._async_update_data()

    restarted = _aggregate_coordinator(
        {
            None: {"queries": [_row(104, "UNKNOWN")]},
            101: {"queries": [_row(101, "UNKNOWN")]},
            100: {"queries": [_row(100)]},
        }
    )
    restarted.store = MemoryStore(first.store.value)
    restarted.cursor = None
    restarted.client_data = {}
    restarted._bootstrapped = False
    await restarted.async_load_cursor()

    await restarted._async_update_data()

    summary = restarted.client_data["192.0.2.10"]
    assert restarted.cursor == 103
    assert summary["query_count"] == 8
    assert summary["classifier_counts"]["1"] == {
        "allowed": 5,
        "blocked": 2,
        "provisional": 1,
    }
    assert "secret-103.example.test" not in repr(restarted.store.value)


@pytest.mark.asyncio
async def test_aggregate_store_failure_rolls_back_counts_and_retry_counts_once() -> None:
    coordinator = _aggregate_coordinator(
        {
            None: {"queries": [_row(103, "GRAVITY")]},
            102: {"queries": [_row(102, "FORWARDED")]},
            101: {"queries": [_row(101, "UNKNOWN")]},
            100: {"queries": [_row(100)]},
        }
    )
    coordinator.store.save_error = OSError("synthetic aggregate Store failure")

    with pytest.raises(UpdateFailed, match="Store"):
        await coordinator._async_update_data()

    assert coordinator.cursor == 100
    assert coordinator.client_data["192.0.2.10"]["query_count"] == 5
    assert coordinator.aggregate_pending == {}

    coordinator.store.save_error = None
    await coordinator._async_update_data()
    assert coordinator.cursor == 103
    assert coordinator.client_data["192.0.2.10"]["query_count"] == 8


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corrupt",
    [
        {"aggregate_pending": None},
        {"aggregate_pending": []},
        {
            "aggregate_pending": {
                "192.0.2.10": {
                    "client_name": "Test client",
                    "client_ip": "192.0.2.10",
                    "counts": {"allowed": True, "blocked": 0, "provisional": 0},
                    "buckets": [],
                }
            }
        },
        {"aggregate_pending_cursor": True},
    ],
)
async def test_malformed_aggregate_store_fails_closed(corrupt: dict[str, Any]) -> None:
    coordinator = _aggregate_coordinator({None: {"queries": [_row(101)]}})
    coordinator.store = MemoryStore(
        {
            "cursor": 100,
            "recovery_cursor": 99,
            "clients": {"192.0.2.10": _summary()},
            **corrupt,
        }
    )
    await coordinator.async_load_cursor()

    with pytest.raises(UpdateFailed, match="datos locales"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_aggregate_store_transition_removes_only_query_details_from_v1_and_v2() -> None:
    source_id = "5be6fe2a-4c73-4798-ae2e-4f1de7de34cd"
    generation = "legacy"
    legacy_data = {"cursor": 100, "clients": {"client": _summary()}, "extension": "secret.example.test"}
    legacy = MemoryStore(legacy_data)
    current = MemoryStore(
        {
            "schema_version": 2,
            "source_id": source_id,
            "source_generation": generation,
            "data": deepcopy(legacy_data),
        }
    )
    store = _IdentityStore(current, legacy, source_id, generation)

    await store.async_load()

    detail_fields = {"last_query", "last_query_id", "recent_queries", "recent_activity"}
    current_summary = current.value["data"]["clients"]["client"]
    legacy_summary = legacy.value["clients"]["client"]
    assert not detail_fields & current_summary.keys()
    assert not detail_fields & legacy_summary.keys()
    assert current.value["data"]["cursor"] == legacy.value["cursor"] == 100
    assert "extension" not in current.value["data"]
    assert "extension" not in legacy.value
    assert current_summary["query_count"] == legacy_summary["query_count"] == 5


@pytest.mark.asyncio
async def test_aggregate_import_from_v1_sanitizes_only_details_before_v2_copy() -> None:
    source_id = "5be6fe2a-4c73-4798-ae2e-4f1de7de34cd"
    legacy_data = {"cursor": 100, "clients": {"client": _summary()}, "extension": "secret.example.test"}
    legacy = MemoryStore(legacy_data)
    current = MemoryStore(None)
    store = _IdentityStore(
        current,
        legacy,
        source_id,
        "legacy",
    )

    prepared = await store.async_load()

    forbidden = {"last_query", "last_query_id", "recent_queries", "recent_activity"}
    assert not forbidden & prepared["clients"]["client"].keys()
    assert not forbidden & current.value["data"]["clients"]["client"].keys()
    assert not forbidden & legacy.value["clients"]["client"].keys()
    assert "extension" not in current.value["data"]
    assert "extension" not in legacy.value
    assert prepared["clients"]["client"]["query_count"] == 5


@pytest.mark.asyncio
async def test_aggregate_scrubs_malformed_clients_and_unknown_root_fields_from_both_stores() -> None:
    source_id = "5be6fe2a-4c73-4798-ae2e-4f1de7de34cd"
    legacy = MemoryStore(
        {
            "cursor": 100,
            "clients": "secret.example.test",
            "private_extension": {"query": "secret.example.test"},
        }
    )
    current = MemoryStore(
        {
            "schema_version": 2,
            "source_id": source_id,
            "source_generation": "legacy",
            "data": {
                "cursor": 100,
                "clients": ["secret.example.test"],
                "private_extension": {"query": "secret.example.test"},
            },
        }
    )
    store = _IdentityStore(current, legacy, source_id, "legacy")

    loaded = await store.async_load()

    assert loaded["cursor"] == 100
    assert "clients" not in loaded
    assert "secret.example.test" not in repr(current.value)
    assert "secret.example.test" not in repr(legacy.value)
    assert "private_extension" not in current.value["data"]
    assert "private_extension" not in legacy.value


@pytest.mark.asyncio
async def test_uninspectable_legacy_root_is_replaced_with_diagnostic_marker() -> None:
    source_id = "5be6fe2a-4c73-4798-ae2e-4f1de7de34cd"
    legacy = MemoryStore([{"query": "secret.example.test"}])
    current = MemoryStore(None)
    store = _IdentityStore(current, legacy, source_id, "legacy")

    loaded = await store.async_load()

    assert isinstance(loaded, list)
    assert legacy.value == {
        "coverage": "unknown",
        "coverage_reason": "invalid_legacy_store",
    }
    assert "secret.example.test" not in repr(legacy.value)
    assert current.value is None


@pytest.mark.asyncio
async def test_aggregate_scrubs_opaque_epochs_and_pending_payloads_in_both_stores() -> None:
    source_id = "5be6fe2a-4c73-4798-ae2e-4f1de7de34cd"
    data = {
        "cursor": 100,
        "coverage": "complete",
        "clients": {
            "192.0.2.10": {
                **_summary(),
                "classifier_counts": {
                    "1": {"allowed": 4, "blocked": 1, "provisional": 0},
                    "private-query.example.test": {
                        "allowed": 1,
                        "blocked": 0,
                        "provisional": 0,
                    },
                },
                "classifier_rates": {
                    "private-query.example.test": {
                        "allowed": 1,
                        "blocked": 0,
                        "provisional": 0,
                    }
                },
            }
        },
        "aggregate_pending": {
            "192.0.2.10": {
                "client_name": "Test client",
                "client_ip": "192.0.2.10",
                "counts": {"allowed": 1, "blocked": 0, "provisional": 0},
                "buckets": [
                    {
                        "second": 1_735_689_600,
                        "classifier_version": 1,
                        "allowed": 1,
                        "blocked": 0,
                        "provisional": 0,
                        "rows": [{"id": 123, "status": "GRAVITY", "domain": "private-query.example.test"}],
                    }
                ],
                "domain": "private-query.example.test",
            }
        },
        "aggregate_pending_cursor": 123,
        "aggregate_pending_time": 1_735_689_600,
        "recovery_cursor": 122,
    }
    legacy = MemoryStore(data)
    current = MemoryStore(
        {
            "schema_version": 2,
            "source_id": source_id,
            "source_generation": "legacy",
            "data": data,
        }
    )
    store = _IdentityStore(current, legacy, source_id, "legacy")

    loaded = await store.async_load()

    summary = loaded["clients"]["192.0.2.10"]
    assert summary["classifier_counts"] == {
        "1": {"allowed": 4, "blocked": 1, "provisional": 0}
    }
    assert "classifier_rates" not in summary
    assert "aggregate_pending" not in loaded
    assert "aggregate_pending_cursor" not in loaded
    assert "aggregate_pending_time" not in loaded
    assert "recovery_cursor" not in loaded
    assert loaded["coverage"] == "unknown"
    assert loaded["coverage_reason"] == "invalid_store_state"
    assert "private-query.example.test" not in repr(loaded)
    assert "private-query.example.test" not in repr(current.value)
    assert "private-query.example.test" not in repr(legacy.value)
    assert current.saves and legacy.saves


@pytest.mark.asyncio
async def test_aggregate_preserves_valid_bounded_pending_aggregates() -> None:
    source_id = "5be6fe2a-4c73-4798-ae2e-4f1de7de34cd"
    legacy = MemoryStore(
        {
            "cursor": 100,
            "clients": {"192.0.2.10": _summary()},
            "aggregate_pending": {
                "192.0.2.10": {
                    "client_name": "Test client",
                    "client_ip": "192.0.2.10",
                    "counts": {"allowed": 1, "blocked": 0, "provisional": 0},
                    "buckets": [
                        {
                            "second": 1_735_689_600,
                            "classifier_version": 1,
                            "allowed": 1,
                            "blocked": 0,
                            "provisional": 0,
                        }
                    ],
                }
            },
            "aggregate_pending_cursor": 101,
            "aggregate_pending_time": 1_735_689_600,
            "recovery_cursor": 100,
        }
    )
    current = MemoryStore(None)
    store = _IdentityStore(current, legacy, source_id, "legacy")

    loaded = await store.async_load()

    assert loaded["aggregate_pending"]["192.0.2.10"]["counts"]["allowed"] == 1
    assert loaded["aggregate_pending_cursor"] == 101
    assert len(loaded["aggregate_pending"]["192.0.2.10"]["buckets"]) == 1
    assert "old.example.test" not in repr(loaded)
    assert "old.example.test" not in repr(current.value)
    assert "old.example.test" not in repr(legacy.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_cursor", [99, 103])
async def test_aggregate_discards_pending_checkpoint_outside_confirmed_cursor_interval(
    recovery_cursor: int,
) -> None:
    source_id = "5be6fe2a-4c73-4798-ae2e-4f1de7de34cd"
    legacy = MemoryStore(
        {
            "cursor": 100,
            "coverage": "complete",
            "clients": {"192.0.2.10": _summary()},
            "aggregate_pending": {
                "192.0.2.10": {
                    "client_name": "Test client",
                    "client_ip": "192.0.2.10",
                    "counts": {"allowed": 2, "blocked": 0, "provisional": 0},
                    "buckets": [],
                }
            },
            "aggregate_pending_cursor": 103,
            "aggregate_pending_time": None,
            "recovery_cursor": recovery_cursor,
        }
    )
    current = MemoryStore(None)
    store = _IdentityStore(current, legacy, source_id, "legacy")

    loaded = await store.async_load()

    assert "aggregate_pending" not in loaded
    assert "aggregate_pending_cursor" not in loaded
    assert "aggregate_pending_time" not in loaded
    assert "recovery_cursor" not in loaded
    assert loaded["coverage"] == "unknown"
    assert loaded["coverage_reason"] == "invalid_store_state"
    assert "aggregate_pending" not in legacy.value
    assert current.value["data"]["coverage_reason"] == "invalid_store_state"


@pytest.mark.asyncio
async def test_v2_scrubbing_retry_completes_legacy_store_after_second_write_failure() -> None:
    source_id = "5be6fe2a-4c73-4798-ae2e-4f1de7de34cd"
    generation = "legacy"
    legacy = MemoryStore({"cursor": 100, "clients": {"client": _summary()}})
    current = MemoryStore(
        {
            "schema_version": 2,
            "source_id": source_id,
            "source_generation": generation,
            "data": {"cursor": 100, "clients": {"client": _summary()}},
        }
    )
    legacy.save_error = OSError("synthetic legacy Store failure")
    store = _IdentityStore(current, legacy, source_id, generation)

    with pytest.raises(OSError, match="legacy Store"):
        await store.async_load()

    # The current Store write succeeded, the legacy write did not. Startup
    # receives an error rather than a successful partial migration.
    assert not {"last_query", "last_query_id", "recent_queries", "recent_activity"} & current.value["data"]["clients"]["client"].keys()
    assert "old.example.test" in repr(legacy.value)

    legacy.save_error = None
    await store.async_load()
    assert "old.example.test" not in repr(legacy.value)
    assert not {"last_query", "last_query_id", "recent_queries", "recent_activity"} & current.value["data"]["clients"]["client"].keys()


@pytest.mark.asyncio
async def test_v1_import_retry_completes_current_store_after_second_write_failure() -> None:
    source_id = "5be6fe2a-4c73-4798-ae2e-4f1de7de34cd"
    legacy = MemoryStore({"cursor": 100, "clients": {"client": _summary()}})
    current = MemoryStore(None)
    current.save_error = OSError("synthetic current Store failure")
    store = _IdentityStore(
        current,
        legacy,
        source_id,
        "legacy",
    )

    with pytest.raises(OSError, match="current Store"):
        await store.async_load()

    # The backup is safely scrubbed before the current envelope is written.
    assert "old.example.test" not in repr(legacy.value)
    assert current.value is None

    current.save_error = None
    prepared = await store.async_load()
    assert current.value["data"]["clients"]["client"]["query_count"] == 5
    assert "old.example.test" not in repr(current.value)
    assert "old.example.test" not in repr(prepared)


@pytest.mark.asyncio
async def test_switch_from_aggregate_pending_to_detail_rereads_from_confirmed_cursor() -> None:
    aggregate = _aggregate_coordinator(
        {
            None: {"queries": [_row(103, "GRAVITY")]},
            102: {"queries": [_row(102, "FORWARDED")]},
            101: {"queries": []},
        }
    )
    await aggregate._async_update_data()
    saved = deepcopy(aggregate.store.value)

    detail = _aggregate_coordinator(
        {
            None: {"queries": [_row(103, "GRAVITY"), _row(102, "FORWARDED")]},
            101: {"queries": [_row(101, "UNKNOWN"), _row(100)]},
        }
    )
    detail.retention_mode = "detail"
    detail.store = MemoryStore(saved)
    detail.cursor = None
    detail.client_data = {}
    detail._bootstrapped = False
    await detail.async_load_cursor()

    assert detail.aggregate_pending == {}
    assert detail.recovery_cursor is None
    await detail._async_update_data()

    assert detail.cursor == 103
    assert detail.client_data["192.0.2.10"]["query_count"] == 8
    assert len(detail.hass.services.calls) == 3
    assert "recent_queries" not in detail.client_data["192.0.2.10"]


@pytest.mark.asyncio
@pytest.mark.parametrize("activity_mode,expected_events", [("all", 3), ("blocked", 1), ("none", 0)])
async def test_activity_filter_does_not_change_accounting(activity_mode: str, expected_events: int) -> None:
    api = StreamingApi(
        {
            None: {"queries": [_row(103, status="GRAVITY"), _row(102, status="FORWARDED")]},
            101: {"queries": [_row(101, status="UNKNOWN"), _row(100)]},
        }
    )
    coordinator = object.__new__(PiHoleQueryCoordinator)
    coordinator.client = api
    coordinator.store = MemoryStore({"cursor": 100, "clients": {"192.0.2.10": _summary()}})
    coordinator.hass = SimpleNamespace(services=Services())
    coordinator.cursor = 100
    coordinator.client_data = {"192.0.2.10": _summary()}
    coordinator._bootstrapped = True
    coordinator.coverage = "complete"
    coordinator.coverage_reason = None
    coordinator.last_query_time = 1_735_689_600
    coordinator.awaiting_generation = False
    coordinator.recovery_cursor = None
    coordinator._client_store_invalid = False
    coordinator._store_state_invalid = False
    coordinator.retention_mode = "detail"
    coordinator.aggregate_pending = {}
    coordinator.activity_mode = activity_mode

    await coordinator._async_update_data()

    assert coordinator.client_data["192.0.2.10"]["query_count"] == 8
    assert len(coordinator.hass.services.calls) == expected_events


def test_aggregate_sensor_attributes_omit_query_rows() -> None:

    coordinator = SimpleNamespace(
        client_data={"192.0.2.10": _summary()},
        retention_mode=RETENTION_AGGREGATE,
    )
    sensor = object.__new__(PiHoleClientSensor)
    sensor.coordinator = coordinator
    sensor._client_key = "192.0.2.10"
    sensor.entity_description = next(
        description for description in SENSOR_DESCRIPTIONS if description.key == "queries"
    )
    attributes = sensor.extra_state_attributes

    assert "old.example.test" not in repr(attributes)
    assert "last_query" not in attributes
    assert "recent_queries" not in attributes
