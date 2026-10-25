"""Recovery behavior across pagination, Activity and Store failure boundaries."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.pihole_queries.api import PiHoleConnectionError
from custom_components.pihole_queries.coordinator import PiHoleQueryCoordinator, _query_id


class _Store:
    def __init__(self, value: dict[str, Any], fail_count: int = 0) -> None:
        self.value = deepcopy(value)
        self.fail_count = fail_count
        self.saves: list[dict[str, Any]] = []

    async def async_load(self) -> dict[str, Any]:
        return deepcopy(self.value)

    async def async_save(self, value: dict[str, Any]) -> None:
        if self.fail_count:
            self.fail_count -= 1
            raise OSError("synthetic Store failure")
        self.value = deepcopy(value)
        self.saves.append(deepcopy(value))


class _Services:
    def __init__(self, fail_at: int | None = None, cancel_at: int | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_at = fail_at
        self.cancel_at = cancel_at

    async def async_call(self, _domain: str, _service: str, data: dict[str, Any], **_kwargs: Any) -> None:
        self.calls.append(data)
        if len(self.calls) == self.cancel_at:
            raise asyncio.CancelledError
        if len(self.calls) == self.fail_at:
            raise HomeAssistantError("synthetic Logbook failure")


class _Api:
    def __init__(self, pages: dict[int | None, Any]) -> None:
        self.pages = pages
        self.calls: list[int | None] = []

    async def async_get_queries(self, *, length: int, cursor: int | None = None) -> dict[str, Any]:
        self.calls.append(cursor)
        response = self.pages.get(cursor, {"queries": []})
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)


def _row(query_id: int, *, time: float = 1_735_689_600, status: str = "FORWARDED") -> dict[str, Any]:
    return {
        "id": query_id,
        "time": time,
        "domain": f"q{query_id}.example.test",
        "client": {"ip": "192.0.2.10", "name": "test-client"},
        "status": status,
    }


@pytest.mark.parametrize("value", [None, "not-an-id", 1.5, True, -1])
def test_invalid_query_ids_are_not_coerced_to_a_different_integer(value: Any) -> None:
    assert _query_id({"id": value}) is None


def _stored(cursor: int = 100) -> dict[str, Any]:
    return {
        "cursor": cursor,
        "coverage": "complete",
        "coverage_reason": None,
        "last_query_time": 1_735_689_600,
        "awaiting_generation": False,
        "clients": {
            "192.0.2.10": {
                "client_name": "test-client",
                "client_ip": "192.0.2.10",
                "query_count": 5,
                "allowed_count": 4,
                "blocked_count": 1,
                "last_query_id": cursor,
                "recent_queries": [],
                "recent_activity": [],
                "classifier_version": 1,
                "classifier_counts": {"1": {"allowed": 4, "blocked": 1, "provisional": 0}},
                "classifier_rates": {"1": {"allowed": 0, "blocked": 0, "provisional": 0}},
            }
        },
    }


def _coordinator(
    api: _Api,
    *,
    stored: dict[str, Any] | None = None,
    store_fail_count: int = 0,
    logbook_fail_at: int | None = None,
    cancel_at: int | None = None,
) -> PiHoleQueryCoordinator:
    coordinator = object.__new__(PiHoleQueryCoordinator)
    coordinator.client = api
    coordinator.store = _Store(stored or _stored(), fail_count=store_fail_count)
    coordinator.hass = SimpleNamespace(
        services=_Services(fail_at=logbook_fail_at, cancel_at=cancel_at)
    )
    saved = stored or _stored()
    coordinator.cursor = saved["cursor"]
    coordinator.client_data = deepcopy(saved["clients"])
    coordinator._bootstrapped = True
    coordinator.coverage = saved.get("coverage", "unknown")
    coordinator.coverage_reason = saved.get("coverage_reason")
    coordinator.last_query_time = saved.get("last_query_time")
    coordinator.awaiting_generation = saved.get("awaiting_generation", False)
    return coordinator


@pytest.mark.asyncio
async def test_pages_process_in_ascending_order_and_commit_only_after_boundary() -> None:
    api = _Api(
        {
            None: {"queries": [_row(103), _row(102)]},
            101: {"queries": [_row(101), _row(100)]},
        }
    )
    coordinator = _coordinator(api)

    data = await coordinator._async_update_data()

    assert api.calls == [None, 101]
    assert len(coordinator.hass.services.calls) == 3
    assert coordinator.cursor == 103
    assert data["192.0.2.10"]["query_count"] == 8
    assert coordinator.coverage == "complete"
    assert coordinator.store.value["cursor"] == 103


@pytest.mark.asyncio
async def test_overlapping_recovery_pages_deduplicate_and_keep_first_observation() -> None:
    api = _Api(
        {
            None: {"queries": [_row(103), _row(102, status="GRAVITY")]},
            101: {
                "queries": [
                    _row(102, status="FORWARDED"),
                    _row(101),
                    _row(100),
                ]
            },
        }
    )
    coordinator = _coordinator(api)

    data = await coordinator._async_update_data()

    assert data["192.0.2.10"]["query_count"] == 8
    assert data["192.0.2.10"]["classifier_counts"]["1"]["blocked"] == 2
    assert "recent_queries" not in data["192.0.2.10"]
    assert len(coordinator.hass.services.calls) == 3


@pytest.mark.asyncio
async def test_name_fallback_reuses_legacy_key_when_case_or_spacing_changes() -> None:
    stored = _stored(cursor=100)
    stored["clients"] = {
        "Living Room": {
            "client_name": "Living Room",
            "client_ip": "Desconocida",
            "query_count": 5,
            "allowed_count": 4,
            "blocked_count": 1,
            "recent_queries": [],
            "recent_activity": [],
        }
    }
    incoming = _row(101)
    incoming["client"] = {"name": "  living   room  "}
    api = _Api({None: {"queries": [incoming, _row(100)]}})
    coordinator = _coordinator(api, stored=stored)

    data = await coordinator._async_update_data()

    assert list(data) == ["Living Room"]
    assert data["Living Room"]["query_count"] == 6
    assert data["Living Room"]["client_name"] == "living   room"


@pytest.mark.asyncio
async def test_logbook_failure_persists_successful_prefix_then_resumes_without_recount() -> None:
    api = _Api({None: {"queries": [_row(102), _row(101), _row(100)]}})
    coordinator = _coordinator(api, logbook_fail_at=2)

    with pytest.raises(UpdateFailed, match="Logbook"):
        await coordinator._async_update_data()

    assert coordinator.cursor == 101
    assert coordinator.client_data["192.0.2.10"]["query_count"] == 6
    assert coordinator.coverage == "unknown"
    assert coordinator.store.value["cursor"] == 101

    coordinator.hass.services.fail_at = None
    data = await coordinator._async_update_data()
    assert data["192.0.2.10"]["query_count"] == 7
    assert coordinator.cursor == 102
    assert coordinator.store.value["cursor"] == 102
    assert len(coordinator.hass.services.calls) == 3


@pytest.mark.asyncio
async def test_store_failure_rolls_back_memory_and_retry_counts_once() -> None:
    api = _Api({None: {"queries": [_row(101), _row(100)]}})
    coordinator = _coordinator(api, store_fail_count=1)

    with pytest.raises(UpdateFailed, match="Store"):
        await coordinator._async_update_data()

    assert coordinator.cursor == 100
    assert coordinator.client_data["192.0.2.10"]["query_count"] == 5
    assert coordinator.coverage == "complete"
    assert len(coordinator.hass.services.calls) == 1

    data = await coordinator._async_update_data()
    assert data["192.0.2.10"]["query_count"] == 6
    assert coordinator.cursor == 101
    # Activity is at-least-once across the Logbook/Store transaction boundary.
    assert len(coordinator.hass.services.calls) == 2


@pytest.mark.asyncio
async def test_api_failure_does_not_advance_cursor_or_change_counters() -> None:
    api = _Api(
        {
            None: {"queries": [_row(102), _row(101)]},
            100: PiHoleConnectionError("synthetic API failure"),
        }
    )
    coordinator = _coordinator(api)

    with pytest.raises(UpdateFailed, match="consultar la API"):
        await coordinator._async_update_data()

    assert coordinator.cursor == 100
    assert coordinator.client_data["192.0.2.10"]["query_count"] == 5
    assert not coordinator.hass.services.calls
    assert coordinator.store.value["cursor"] == 100


@pytest.mark.asyncio
async def test_lower_newest_id_marks_reset_and_blocks_cross_generation_counts() -> None:
    api = _Api({None: {"queries": [_row(490)]}, 489: {"queries": [_row(490)]}})
    coordinator = _coordinator(api, stored=_stored(cursor=500))

    data = await coordinator._async_update_data()

    assert coordinator.cursor == 490
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "query_id_reset"
    assert coordinator.awaiting_generation is True
    assert data["192.0.2.10"]["query_count"] == 5
    assert not coordinator.hass.services.calls
    assert coordinator.store.value["awaiting_generation"] is True
    await coordinator._async_update_data()
    assert len(api.calls) == 2


@pytest.mark.asyncio
async def test_failed_reset_commit_rolls_back_generation_gate_for_retry() -> None:
    api = _Api({None: {"queries": [_row(490)]}, 489: {"queries": [_row(490)]}})
    coordinator = _coordinator(api, stored=_stored(cursor=500), store_fail_count=1)

    with pytest.raises(UpdateFailed, match="Store"):
        await coordinator._async_update_data()

    assert coordinator.cursor == 500
    assert coordinator.coverage == "complete"
    assert coordinator.awaiting_generation is False

    await coordinator._async_update_data()
    assert coordinator.cursor == 490
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "query_id_reset"
    assert coordinator.awaiting_generation is True


@pytest.mark.asyncio
async def test_empty_fresh_baseline_does_not_create_cursor_zero_and_stall_future_queries() -> None:
    api = _Api({None: {"queries": []}})
    coordinator = _coordinator(api, stored=None)
    coordinator.cursor = None
    coordinator._bootstrapped = False
    coordinator.client_data = {}

    await coordinator._async_update_data()
    assert coordinator.cursor is None
    assert coordinator._bootstrapped is False

    api.pages[None] = {"queries": [_row(101)]}
    await coordinator._async_update_data()
    assert coordinator.cursor == 101
    assert coordinator.coverage == "complete"
    assert coordinator.client_data["192.0.2.10"]["query_count"] == 0

    api.pages[None] = {"queries": [_row(102), _row(101)]}
    data = await coordinator._async_update_data()
    assert data["192.0.2.10"]["query_count"] == 1


@pytest.mark.asyncio
async def test_invalid_legacy_cursor_starts_unknown_until_new_baseline_is_reached() -> None:
    api = _Api({None: {"queries": []}})
    coordinator = _coordinator(api)
    coordinator.store.value = {"clients": deepcopy(coordinator.client_data)}
    coordinator.cursor = None
    coordinator._bootstrapped = False
    coordinator.coverage = "unknown"
    coordinator.coverage_reason = "no_baseline"

    await coordinator.async_load_cursor()
    await coordinator._async_update_data()
    api.pages[None] = {"queries": [_row(101)]}
    await coordinator._async_update_data()
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "invalid_legacy_cursor"
    assert coordinator.cursor == 101

    coordinator.client.pages[None] = {"queries": [_row(102), _row(101)]}
    data = await coordinator._async_update_data()
    assert coordinator.coverage == "complete"
    assert data["192.0.2.10"]["query_count"] == 6


@pytest.mark.asyncio
async def test_non_mapping_legacy_store_is_ignored_as_unknown() -> None:
    api = _Api({None: {"queries": []}})
    coordinator = _coordinator(api)
    coordinator.store.value = ["not", "a", "mapping"]
    coordinator.cursor = None
    coordinator._bootstrapped = False

    await coordinator.async_load_cursor()

    assert coordinator.cursor is None
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "invalid_legacy_store"

    api.pages[None] = {"queries": [_row(101)]}
    await coordinator._async_update_data()
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "invalid_legacy_store"
    assert coordinator.cursor == 101

    api.pages[None] = {"queries": [_row(102), _row(101)]}
    await coordinator._async_update_data()
    assert coordinator.coverage == "complete"


@pytest.mark.asyncio
async def test_legacy_query_details_are_discarded_without_invalidating_totals() -> None:
    api = _Api({None: {"queries": [_row(101), _row(100)]}})
    coordinator = _coordinator(api)
    coordinator.store.value["clients"]["192.0.2.10"].update(
        {"last_query": {"domain": "private.example.test"}, "recent_queries": [{"id": "bad"}]}
    )

    await coordinator.async_load_cursor()
    data = await coordinator._async_update_data()

    assert coordinator.cursor == 101
    assert coordinator.coverage == "complete"
    assert "recent_queries" not in data["192.0.2.10"]
    assert "private.example.test" not in repr(coordinator.store.value)
    assert len(coordinator.hass.services.calls) == 1


@pytest.mark.asyncio
async def test_invalid_query_count_fails_closed_without_relogging() -> None:
    api = _Api({None: {"queries": [_row(101), _row(100)]}})
    coordinator = _coordinator(api)
    coordinator.store.value["clients"]["192.0.2.10"]["query_count"] = "corrupt"

    await coordinator.async_load_cursor()
    with pytest.raises(UpdateFailed, match="datos locales"):
        await coordinator._async_update_data()

    assert coordinator.cursor == 100
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "invalid_client_summaries"
    assert coordinator.hass.services.calls == []
    assert coordinator.store.value["cursor"] == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("clients", [None, "corrupt", ["corrupt"]])
async def test_non_mapping_client_summaries_keep_coverage_unknown_and_counters_untouched(
    clients: Any,
) -> None:
    api = _Api({None: {"queries": [_row(101), _row(100)]}})
    coordinator = _coordinator(api)
    coordinator.store.value["clients"] = clients

    await coordinator.async_load_cursor()
    with pytest.raises(UpdateFailed, match="datos locales"):
        await coordinator._async_update_data()

    assert coordinator.cursor == 100
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "invalid_client_summaries"
    assert coordinator.client_data == {}
    assert coordinator.hass.services.calls == []


@pytest.mark.asyncio
async def test_missing_client_map_is_legacy_compatible_but_remains_unknown() -> None:
    api = _Api({None: {"queries": [_row(101), _row(100)]}})
    coordinator = _coordinator(api)
    coordinator.store.value.pop("clients")

    await coordinator.async_load_cursor()
    data = await coordinator._async_update_data()

    assert coordinator.cursor == 101
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "missing_client_summaries"
    assert data["192.0.2.10"]["query_count"] == 1
    assert len(coordinator.hass.services.calls) == 1


@pytest.mark.asyncio
async def test_missing_client_map_does_not_override_invalid_legacy_cursor() -> None:
    api = _Api({None: {"queries": [_row(101)]}})
    coordinator = _coordinator(api)
    coordinator.store.value = {"cursor": "bad"}
    coordinator.cursor = None
    coordinator.client_data = {}
    coordinator._bootstrapped = False

    await coordinator.async_load_cursor()
    await coordinator._async_update_data()

    assert coordinator.cursor == 101
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "invalid_legacy_cursor"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "classifier_counts",
    [
        "corrupt",
        None,
        {},
        {"1": {"allowed": "corrupt", "blocked": 1, "provisional": 0}},
        {"1": {"allowed": 4}},
    ],
)
async def test_invalid_classifier_counts_fail_closed_without_resetting_series(
    classifier_counts: Any,
) -> None:
    api = _Api({None: {"queries": [_row(101), _row(100)]}})
    coordinator = _coordinator(api)
    coordinator.store.value["clients"]["192.0.2.10"]["classifier_counts"] = deepcopy(
        classifier_counts
    )

    await coordinator.async_load_cursor()
    with pytest.raises(UpdateFailed, match="datos locales"):
        await coordinator._async_update_data()

    assert coordinator.cursor == 100
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "invalid_client_summaries"
    assert coordinator.hass.services.calls == []
    assert coordinator.store.value["clients"]["192.0.2.10"]["classifier_counts"] == classifier_counts


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_cursor", [99, 103])
async def test_invalid_aggregate_pending_cursor_interval_fails_closed(
    recovery_cursor: int,
) -> None:
    stored = _stored(100)
    stored.update(
        {
            "aggregate_pending": {
                "192.0.2.10": {
                    "client_name": "test-client",
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
    api = _Api({99: {"queries": [_row(99)]}, 103: {"queries": [_row(103)]}})
    coordinator = _coordinator(api, stored=stored)
    coordinator.retention_mode = "aggregate"
    coordinator.aggregate_pending = {}
    coordinator.aggregate_pending_cursor = None
    coordinator.aggregate_pending_time = None
    coordinator._store_state_invalid = False
    coordinator._client_store_invalid = False

    await coordinator.async_load_cursor()
    with pytest.raises(UpdateFailed, match="datos locales"):
        await coordinator._async_update_data()

    assert coordinator.cursor == 100
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "invalid_store_state"
    assert coordinator.store.value["cursor"] == 100
    assert coordinator.hass.services.calls == []


@pytest.mark.asyncio
async def test_malformed_generation_gate_fails_closed_without_mixing_series() -> None:
    stored = _stored(490)
    stored["awaiting_generation"] = "corrupt"
    api = _Api({None: {"queries": [_row(491), _row(490)]}})
    coordinator = _coordinator(api, stored=stored)

    await coordinator.async_load_cursor()
    with pytest.raises(UpdateFailed, match="datos locales"):
        await coordinator._async_update_data()

    assert coordinator.cursor == 490
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "invalid_store_state"
    assert coordinator.hass.services.calls == []
    assert coordinator.store.value["awaiting_generation"] == "corrupt"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("coverage", []),
        ("coverage", {}),
        ("coverage", "broken"),
        ("coverage_reason", []),
        ("last_query_time", "corrupt"),
        ("last_query_time", True),
        ("last_query_time", {}),
        ("awaiting_generation", []),
        ("recovery_cursor", "bad-id"),
        ("recovery_cursor", True),
        ("recovery_cursor", {}),
    ],
)
async def test_malformed_store_root_fields_fail_closed(field: str, value: Any) -> None:
    api = _Api({None: {"queries": [_row(101), _row(100)], "earliest_timestamp": 1_735_689_700}})
    coordinator = _coordinator(api)
    coordinator.store.value[field] = deepcopy(value)

    await coordinator.async_load_cursor()
    with pytest.raises(UpdateFailed, match="datos locales"):
        await coordinator._async_update_data()

    assert coordinator.cursor == 100
    assert coordinator.coverage == "unknown"
    assert coordinator.hass.services.calls == []
    assert coordinator.store.value[field] == value


@pytest.mark.asyncio
async def test_null_last_query_time_remains_valid_for_legacy_baseline() -> None:
    api = _Api({None: {"queries": [_row(101), _row(100)]}})
    coordinator = _coordinator(api)
    coordinator.store.value["last_query_time"] = None

    await coordinator.async_load_cursor()
    await coordinator._async_update_data()

    assert coordinator.cursor == 101
    assert coordinator.coverage == "complete"
    assert coordinator.last_query_time == _row(101)["time"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["query_count"])
async def test_missing_or_malformed_client_counters_fail_closed(field: str) -> None:
    api = _Api({None: {"queries": [_row(101), _row(100)]}})
    coordinator = _coordinator(api)
    coordinator.store.value["clients"]["192.0.2.10"].pop(field)

    await coordinator.async_load_cursor()
    with pytest.raises(UpdateFailed, match="datos locales"):
        await coordinator._async_update_data()

    assert coordinator.cursor == 100
    assert coordinator.coverage == "unknown"
    assert coordinator.hass.services.calls == []


@pytest.mark.asyncio
async def test_earliest_timestamp_detects_retention_only_by_comparing_query_times() -> None:
    api = _Api(
        {
            None: {
                "queries": [_row(102, time=1_735_689_960), _row(101, time=1_735_689_930)],
                "earliest_timestamp": 1_735_689_500,
            },
            100: {"queries": [_row(100)]},
        }
    )
    coordinator = _coordinator(api)

    await coordinator._async_update_data()

    assert coordinator.coverage == "complete"
    # The smallest query ID is never compared to the server's timestamp field.
    assert coordinator.coverage_reason is None

    truncated_api = _Api(
        {
            None: {
                "queries": [_row(102, time=1_735_690_000), _row(101, time=1_735_689_930)],
                "earliest_timestamp": 1_735_689_630,
            },
            100: {"queries": [_row(100)]},
        }
    )
    truncated = _coordinator(truncated_api)
    await truncated._async_update_data()
    assert truncated.coverage == "partial"
    assert truncated.coverage_reason == "retention_truncation"


@pytest.mark.asyncio
async def test_empty_or_unreached_history_is_not_reported_complete() -> None:
    empty = _coordinator(_Api({None: {"queries": []}}))
    await empty._async_update_data()
    assert empty.coverage == "unknown"
    assert empty.coverage_reason == "empty_history"
    assert empty.client_data["192.0.2.10"]["query_count"] == 5

    unreached = _coordinator(
        _Api({None: {"queries": [_row(102), _row(101)]}, 100: {"queries": []}})
    )
    await unreached._async_update_data()
    assert unreached.cursor == 100
    assert unreached.recovery_cursor == 100
    assert unreached.coverage == "unknown"
    assert unreached.coverage_reason == "cursor_not_reached"
    assert unreached.client_data["192.0.2.10"]["query_count"] == 5


@pytest.mark.asyncio
async def test_transient_empty_page_resumes_without_losing_the_old_cursor() -> None:
    api = _Api({None: {"queries": [_row(102), _row(101)]}, 100: {"queries": []}})
    coordinator = _coordinator(api)

    await coordinator._async_update_data()
    assert coordinator.cursor == 100
    assert coordinator.recovery_cursor == 100
    assert not coordinator.hass.services.calls

    api.pages[100] = {"queries": [_row(100)]}
    data = await coordinator._async_update_data()
    assert data["192.0.2.10"]["query_count"] == 7
    assert coordinator.cursor == 102
    assert coordinator.recovery_cursor is None
    assert coordinator.coverage == "complete"
    assert len(coordinator.hass.services.calls) == 2


@pytest.mark.asyncio
async def test_newer_ids_without_cursor_token_remain_ambiguous_and_partial() -> None:
    api = _Api({None: {"queries": [_row(502), _row(501)]}, 500: {"queries": []}})
    coordinator = _coordinator(api, stored=_stored(cursor=500))

    await coordinator._async_update_data()

    assert coordinator.cursor == 500
    assert coordinator.recovery_cursor == 500
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "cursor_not_reached"
    assert coordinator.client_data["192.0.2.10"]["query_count"] == 5


@pytest.mark.asyncio
async def test_rows_without_ids_are_skipped_and_make_coverage_partial() -> None:
    malformed = {"time": 1_735_689_600, "domain": "bad.example.test"}
    api = _Api({None: {"queries": [_row(101), malformed, _row(100)]}})
    coordinator = _coordinator(api)

    data = await coordinator._async_update_data()

    assert data["192.0.2.10"]["query_count"] == 6
    assert coordinator.cursor == 101
    assert coordinator.coverage == "partial"
    assert coordinator.coverage_reason == "invalid_query_ids"
    assert len(coordinator.hass.services.calls) == 1

    no_ids = _coordinator(_Api({None: {"queries": [malformed]}}))
    await no_ids._async_update_data()
    assert no_ids.coverage == "partial"
    assert no_ids.coverage_reason == "invalid_query_ids"


@pytest.mark.asyncio
async def test_cancellation_is_propagated_and_persists_only_confirmed_prefix() -> None:
    api = _Api({None: {"queries": [_row(102), _row(101), _row(100)]}})
    coordinator = _coordinator(api, cancel_at=2)

    with pytest.raises(asyncio.CancelledError):
        await coordinator._async_update_data()

    assert coordinator.cursor == 101
    assert coordinator.store.value["cursor"] == 101
    assert coordinator.client_data["192.0.2.10"]["query_count"] == 6
    assert coordinator.coverage == "unknown"
