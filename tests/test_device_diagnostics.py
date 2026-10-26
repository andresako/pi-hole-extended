"""Downloadable diagnostics and client device removal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from custom_components.pihole_queries import async_remove_config_entry_device
from custom_components.pihole_queries.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    DOMAIN,
    LEGACY_SOURCE_GENERATION,
    CoverageReason,
)
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
            CONF_SOURCE_GENERATION: LEGACY_SOURCE_GENERATION,
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
        update_interval=timedelta(seconds=15),
        client_data={
            "192.0.2.10": {
                "client_name": "secret-laptop",
                "client_ip": "192.0.2.10",
                "query_count": 7,
                "queries_last_minute": 2,
                "classifier_version": 1,
            }
        },
    )


async def test_diagnostics_redact_credentials_and_clients() -> None:
    entry = _entry()
    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: {"coordinator": _coordinator()}}}
    )
    result = await async_get_config_entry_diagnostics(hass, entry)

    text = repr(result)
    for secret in ("synthetic-test-password", "192.0.2.2", "192.0.2.10", "secret-laptop", SOURCE_ID):
        assert secret not in text
    assert result["coordinator"]["cursor"] == 42
    assert result["coordinator"]["coverage_reason"] == "no_baseline"
    assert result["client_count"] == 1
    assert result["clients"][0]["query_count"] == 7


async def test_only_inactive_client_devices_can_be_removed() -> None:
    entry = _entry()
    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: {"coordinator": _coordinator()}}}
    )
    active = SimpleNamespace(identifiers={(DOMAIN, f"{entry.entry_id}_192.0.2.10")})
    gone = SimpleNamespace(identifiers={(DOMAIN, f"{entry.entry_id}_192.0.2.99")})

    assert await async_remove_config_entry_device(hass, entry, active) is False
    assert await async_remove_config_entry_device(hass, entry, gone) is True
