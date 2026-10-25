"""Tests for the activity websocket command logic."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.pihole_queries import websocket as ws
from custom_components.pihole_queries.const import DOMAIN

ENTITY = "sensor.phe_192_0_2_10_consultas_dns"


def _registry(entries: dict[str, SimpleNamespace]):
    return MagicMock(async_get=lambda entity_id: entries.get(entity_id))


def _msg(**overrides):
    base = {"entity_ids": [ENTITY], "bucket": "30m", "span": "1d"}
    return {**base, **overrides}


async def _run(msg, entries, stats=None):
    recorder = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=stats or {})
    )
    with (
        patch.object(ws.er, "async_get", return_value=_registry(entries)),
        patch.object(ws, "get_instance", return_value=recorder),
    ):
        return await ws.async_client_activity(MagicMock(), msg), recorder


QUERIES = {
    ENTITY: SimpleNamespace(platform=DOMAIN, translation_key="queries"),
}


async def test_returns_series_for_integration_sensor() -> None:
    rows = [{"start": 1_735_689_600 + i * 300, "change": 10.0} for i in range(6)]
    result, recorder = await _run(_msg(), QUERIES, {ENTITY: rows})
    assert result["period"] == "5minute"
    assert result["bucket_seconds"] == 1800
    assert ENTITY in result["series"]
    assert result["series"][ENTITY]["has_baseline"] is False
    fetch_args = recorder.async_add_executor_job.call_args.args
    assert fetch_args[4] == {ENTITY}
    assert fetch_args[5] == "5minute"


async def test_rejects_foreign_or_wrong_sensor() -> None:
    entries = {
        ENTITY: SimpleNamespace(platform="other", translation_key="queries"),
    }
    with pytest.raises(ws.ActivityRequestError) as err:
        await _run(_msg(), entries)
    assert err.value.code == "invalid_entity"

    entries = {
        ENTITY: SimpleNamespace(platform=DOMAIN, translation_key="queries_last_minute"),
    }
    with pytest.raises(ws.ActivityRequestError):
        await _run(_msg(), entries)

    with pytest.raises(ws.ActivityRequestError):
        await _run(_msg(), {})


async def test_rejects_unavailable_combinations() -> None:
    with pytest.raises(ws.ActivityRequestError) as err:
        await _run(_msg(bucket="5m", span="30d"), QUERIES)
    assert err.value.code == "fine_buckets_unavailable"


async def test_hourly_buckets_use_hourly_statistics() -> None:
    result, recorder = await _run(_msg(bucket="1h", span="30d"), QUERIES)
    assert result["period"] == "hour"
    assert recorder.async_add_executor_job.call_args.args[5] == "hour"
