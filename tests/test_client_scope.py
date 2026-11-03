"""The client scope limits devices to Pi-hole's local DNS records."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.pihole_queries.api import PiHoleClient, PiHoleConnectionError
from custom_components.pihole_queries.config_flow import _schema
from custom_components.pihole_queries.const import (
    CLIENT_SCOPE_ALL,
    CLIENT_SCOPE_LOCAL_DNS,
    CONF_CLIENT_SCOPE,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USE_HTTPS,
    OTHER_CLIENTS_KEY,
)
from custom_components.pihole_queries.coordinator import PiHoleQueryCoordinator
from custom_components.pihole_queries.sensor import client_device_info


def _coordinator(scope: str, hosts: dict[str, str] | None = None) -> PiHoleQueryCoordinator:
    coordinator = object.__new__(PiHoleQueryCoordinator)
    coordinator.client_data = {}
    coordinator.client_scope = scope
    coordinator.local_hosts = hosts
    coordinator._local_hosts_read_at = 0.0
    coordinator.client = SimpleNamespace(async_get_local_hosts=AsyncMock())
    return coordinator


def _query(ip: str, name: str = "stale.home") -> dict:
    return {"id": 1, "time": 1.0, "status": "FORWARDED", "client": {"ip": ip, "name": name}}


def _client(hosts: object) -> PiHoleClient:
    client = PiHoleClient(
        SimpleNamespace(),
        {CONF_HOST: "192.0.2.2", CONF_PORT: 80, CONF_USE_HTTPS: False, CONF_PASSWORD: "x"},
    )
    client._async_get = AsyncMock(return_value=hosts)
    return client


def test_option_defaults_to_all_clients() -> None:
    schema = _schema(include_connection=False, include_retention=True)

    assert schema({CONF_SCAN_INTERVAL: 15})[CONF_CLIENT_SCOPE] == CLIENT_SCOPE_ALL


def test_all_clients_keeps_the_name_from_pihole() -> None:
    coordinator = _coordinator(CLIENT_SCOPE_ALL)

    assert coordinator._resolve_client(_query("192.0.2.10")) == (
        "192.0.2.10",
        "stale.home",
        "192.0.2.10",
    )


def test_local_record_names_the_client() -> None:
    coordinator = _coordinator(CLIENT_SCOPE_LOCAL_DNS, {"192.0.2.10": "real.home"})

    assert coordinator._resolve_client(_query("192.0.2.10")) == (
        "192.0.2.10",
        "real.home",
        "192.0.2.10",
    )


async def test_clients_without_a_record_share_the_other_device() -> None:
    coordinator = _coordinator(CLIENT_SCOPE_LOCAL_DNS, {"192.0.2.10": "real.home"})
    coordinator.client_data["192.0.2.20"] = {"client_ip": "192.0.2.20", "query_count": 5}

    coordinator._update_client(_query("192.0.2.20"), 1, count_as_new=True)
    coordinator._update_client(_query("192.0.2.21"), 2, count_as_new=True)

    assert coordinator.client_data[OTHER_CLIENTS_KEY]["query_count"] == 2
    assert coordinator.client_data["192.0.2.20"]["query_count"] == 5
    assert "192.0.2.21" not in coordinator.client_data


@pytest.mark.parametrize(("language", "name"), [("en", "Other devices"), ("es", "Otros dispositivos")])
def test_other_device_name_follows_the_language(language: str, name: str) -> None:
    coordinator = _coordinator(CLIENT_SCOPE_LOCAL_DNS, {})
    coordinator.hass = SimpleNamespace(config=SimpleNamespace(language=language))
    coordinator._update_client(_query("192.0.2.20"), 1, count_as_new=True)
    entry = SimpleNamespace(entry_id="entry", unique_id="unique", data={})

    info = client_device_info(coordinator, entry, OTHER_CLIENTS_KEY)

    assert info["translation_placeholders"] == {"client_name": name}


async def test_hosts_are_read_once_per_refresh_period() -> None:
    coordinator = _coordinator(CLIENT_SCOPE_LOCAL_DNS)
    coordinator.client.async_get_local_hosts.return_value = {"192.0.2.10": "real.home"}

    await coordinator._async_refresh_local_hosts()
    await coordinator._async_refresh_local_hosts()

    coordinator.client.async_get_local_hosts.assert_awaited_once()
    assert coordinator.local_hosts == {"192.0.2.10": "real.home"}


async def test_all_clients_never_reads_hosts() -> None:
    coordinator = _coordinator(CLIENT_SCOPE_ALL)

    await coordinator._async_refresh_local_hosts()

    coordinator.client.async_get_local_hosts.assert_not_awaited()


async def test_first_hosts_failure_fails_the_poll() -> None:
    coordinator = _coordinator(CLIENT_SCOPE_LOCAL_DNS)
    coordinator.client.async_get_local_hosts.side_effect = PiHoleConnectionError("down")

    with pytest.raises(PiHoleConnectionError):
        await coordinator._async_refresh_local_hosts()

    assert coordinator.last_api_attempt_successful is False


async def test_later_hosts_failure_keeps_the_previous_list() -> None:
    coordinator = _coordinator(CLIENT_SCOPE_LOCAL_DNS, {"192.0.2.10": "real.home"})
    coordinator.client.async_get_local_hosts.side_effect = PiHoleConnectionError("down")

    await coordinator._async_refresh_local_hosts()

    assert coordinator.local_hosts == {"192.0.2.10": "real.home"}


async def test_hosts_parsing_uses_the_first_name_and_skips_invalid_records() -> None:
    client = _client(
        {
            "config": {
                "dns": {
                    "hosts": [
                        "192.0.2.10 real.home real",
                        "192.0.2.10 duplicate.home",
                        "2001:0db8::1 six.home",
                        "not-an-ip name.home",
                        "192.0.2.11",
                        7,
                    ]
                }
            }
        }
    )

    assert await client.async_get_local_hosts() == {
        "192.0.2.10": "real.home",
        "2001:db8::1": "six.home",
    }


@pytest.mark.parametrize("payload", [{}, {"config": {"dns": {"hosts": "x"}}}, None])
async def test_invalid_hosts_response_is_an_error(payload: object) -> None:
    with pytest.raises(PiHoleConnectionError):
        await _client(payload).async_get_local_hosts()
