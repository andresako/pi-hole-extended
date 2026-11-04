"""Downloadable diagnostics and client device removal."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.pihole_queries import async_remove_config_entry_device
from custom_components.pihole_queries.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    DOMAIN,
    FIRST_SOURCE_GENERATION,
    CoverageReason,
)
from custom_components.pihole_queries.coordinator import PiHoleQueryCoordinator
from custom_components.pihole_queries.diagnostics import (
    async_get_config_entry_diagnostics,
)

SOURCE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _entry() -> SimpleNamespace:
    return SimpleNamespace(
        entry_id="entry-1",
        unique_id="http://192.0.2.2:80",
        data={
            CONF_HOST: "192.0.2.2",
            CONF_PASSWORD: "synthetic-test-password",
            CONF_SOURCE_ID: SOURCE_ID,
            CONF_SOURCE_GENERATION: FIRST_SOURCE_GENERATION,
        },
        options={"scan_interval": 15},
    )


def _coordinator() -> SimpleNamespace:
    return SimpleNamespace(
        cursor=42,
        coverage="complete",
        coverage_reason=CoverageReason.NO_BASELINE,
        awaiting_generation=False,
        recovery_cursor=None,
        last_query_time=1_735_689_600.0,
        last_update_success=True,
        last_successful_read=datetime(2025, 1, 1, tzinfo=UTC),
        retention_mode="detail",
        activity_mode="all",
        client_scope="all",
        local_hosts=None,
        update_interval=timedelta(seconds=15),
        client_data={
            "192.0.2.10": {
                "client_name": "secret-laptop",
                "client_ip": "192.0.2.10",
                "query_count": 7,
                "queries_last_minute": 2,
            }
        },
    )


async def test_diagnostics_redact_credentials_and_clients() -> None:
    entry = _entry()
    hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: {"coordinator": _coordinator()}}})
    result = await async_get_config_entry_diagnostics(hass, entry)

    text = repr(result)
    for secret in (
        "synthetic-test-password",
        "192.0.2.2",
        "192.0.2.10",
        "secret-laptop",
        SOURCE_ID,
    ):
        assert secret not in text
    assert result["coordinator"]["cursor"] == 42
    assert result["coordinator"]["coverage_reason"] == "no_baseline"
    assert result["client_count"] == 1
    assert result["clients"][0]["query_count"] == 7


def _removal_coordinator(*clients: str) -> PiHoleQueryCoordinator:
    coordinator = PiHoleQueryCoordinator.__new__(PiHoleQueryCoordinator)
    coordinator.client_data = {key: {"client_ip": key, "query_count": 7} for key in clients}
    coordinator.aggregate_pending = {}
    coordinator._client_removed_listeners = []
    coordinator._save_cursor = AsyncMock()
    return coordinator


def _device(entry: SimpleNamespace, key: str) -> SimpleNamespace:
    return SimpleNamespace(
        identifiers={
            (
                DOMAIN,
                f"{SOURCE_ID}_first_{key}"
                if key != "diagnostics"
                else f"{entry.entry_id}_diagnostics",
            )
        }
    )


async def test_known_client_device_is_removed_and_forgotten() -> None:
    entry = _entry()
    coordinator = _removal_coordinator("192.0.2.10", "192.0.2.11")
    removed: list[str] = []
    coordinator.async_add_client_removed_listener(removed.append)
    hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: {"coordinator": coordinator}}})

    assert await async_remove_config_entry_device(hass, entry, _device(entry, "192.0.2.10"))

    assert set(coordinator.client_data) == {"192.0.2.11"}
    coordinator._save_cursor.assert_awaited_once()
    assert removed == ["192.0.2.10"]


async def test_device_without_client_data_can_be_removed() -> None:
    entry = _entry()
    coordinator = _removal_coordinator("192.0.2.10")
    hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: {"coordinator": coordinator}}})

    assert await async_remove_config_entry_device(hass, entry, _device(entry, "192.0.2.99"))

    assert set(coordinator.client_data) == {"192.0.2.10"}
    coordinator._save_cursor.assert_not_awaited()


async def test_client_is_kept_when_its_removal_cannot_be_saved() -> None:
    entry = _entry()
    coordinator = _removal_coordinator("192.0.2.10")
    coordinator._save_cursor.side_effect = OSError("disk full")
    removed: list[str] = []
    coordinator.async_add_client_removed_listener(removed.append)
    hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: {"coordinator": coordinator}}})

    assert not await async_remove_config_entry_device(hass, entry, _device(entry, "192.0.2.10"))

    assert set(coordinator.client_data) == {"192.0.2.10"}
    assert removed == []


async def test_diagnostics_device_cannot_be_removed() -> None:
    entry = _entry()
    coordinator = _removal_coordinator("192.0.2.10")
    hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: {"coordinator": coordinator}}})

    assert not await async_remove_config_entry_device(hass, entry, _device(entry, "diagnostics"))


async def test_device_of_an_unloaded_entry_cannot_be_removed() -> None:
    entry = _entry()
    hass = SimpleNamespace(data={DOMAIN: {}})

    assert not await async_remove_config_entry_device(hass, entry, _device(entry, "192.0.2.10"))


async def test_removed_client_is_not_restored_by_a_poll_in_progress() -> None:
    coordinator = _removal_coordinator("192.0.2.10")
    started = asyncio.Event()
    release = asyncio.Event()

    async def snapshot() -> dict:
        checkpoint = dict(coordinator.client_data)
        started.set()
        await release.wait()
        coordinator.client_data = checkpoint  # what a failed poll's rollback does
        return checkpoint

    coordinator._async_update_data_snapshot = snapshot
    poll = asyncio.create_task(coordinator._async_update_data())
    await started.wait()
    forget = asyncio.create_task(coordinator.async_forget_client("192.0.2.10"))
    await asyncio.sleep(0)
    release.set()
    await poll

    assert await forget
    assert coordinator.client_data == {}
