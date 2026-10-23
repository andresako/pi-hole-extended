"""Behavioral tests for the Pi-hole API client using local HTTP servers."""

from __future__ import annotations

import ssl

import aiohttp
import pytest
import trustme
from aiohttp import web

from custom_components.pihole_queries.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_USE_HTTPS,
)
from custom_components.pihole_queries.api import (
    PiHoleAuthenticationError,
    PiHoleCertificateError,
    PiHoleClient,
    PiHoleConnectionError,
)


@pytest.mark.asyncio
async def test_authenticate_and_fetch_queries(
    aiohttp_client, pi_hole_app, api_config, api_plan, query_fixture
):
    server = await aiohttp_client(pi_hole_app)
    api_config[CONF_HOST] = "127.0.0.1"
    api_config[CONF_PORT] = server.server.port
    async with aiohttp.ClientSession() as session:
        client = PiHoleClient(session, api_config)
        result = await client.async_get_queries(length=2, cursor=1000)

    assert result["queries"] == query_fixture
    assert api_plan.auth_calls == 1
    assert len(api_plan.query_calls) == 1
    assert api_config[CONF_PORT] == server.server.port


@pytest.mark.asyncio
async def test_authentication_rejection_is_translated(
    aiohttp_client, pi_hole_app, api_config, api_plan
):
    api_plan.auth_responses.append((401, {"error": "unauthorized"}))
    server = await aiohttp_client(pi_hole_app)
    api_config[CONF_PORT] = server.server.port
    async with aiohttp.ClientSession() as session:
        client = PiHoleClient(session, api_config)
        with pytest.raises(PiHoleAuthenticationError):
            await client.async_authenticate()


@pytest.mark.parametrize(
    "payload",
    [
        {"session": None},
        {"session": "invalid"},
        {"session": ["invalid"]},
        {"session": {"sid": "sid-invalid"}},
        {"session": {"valid": "yes", "sid": "sid-invalid"}},
        {"session": {"valid": True, "sid": ["invalid"]}},
        {"session": {"valid": True, "sid": ""}},
        {"session": {"valid": True, "sid": "   "}},
        {"session": {"valid": True, "sid": "bad\r\nsid"}},
    ],
)
@pytest.mark.asyncio
async def test_malformed_authentication_response_is_connection_error(
    payload, aiohttp_client, pi_hole_app, api_config, api_plan
):
    api_plan.auth_responses.append((200, payload))
    server = await aiohttp_client(pi_hole_app)
    api_config[CONF_PORT] = server.server.port
    async with aiohttp.ClientSession() as session:
        client = PiHoleClient(session, api_config)
        with pytest.raises(PiHoleConnectionError, match="Respuesta de /api/auth no válida"):
            await client.async_authenticate()


@pytest.mark.asyncio
async def test_expired_sid_is_renewed_once(
    aiohttp_client, pi_hole_app, api_config, api_plan, query_fixture
):
    api_plan.query_responses.extend(
        [
            (401, {"error": "expired"}),
            (200, {"queries": query_fixture}),
        ]
    )
    server = await aiohttp_client(pi_hole_app)
    api_config[CONF_PORT] = server.server.port
    async with aiohttp.ClientSession() as session:
        client = PiHoleClient(session, api_config)
        result = await client.async_get_queries(length=2)

    assert result["queries"] == query_fixture
    assert api_plan.auth_calls == 2
    assert [headers["X-FTL-SID"] for headers in api_plan.query_calls] == ["sid-1", "sid-2"]


@pytest.mark.asyncio
async def test_invalid_json_is_translated(
    aiohttp_client, pi_hole_app, api_config, api_plan
):
    async def invalid_json(_request: web.Request) -> web.Response:
        return web.Response(text="not-json", content_type="application/json")

    app = web.Application()
    async def authenticate(_request: web.Request) -> web.Response:
        return web.json_response(
            {"session": {"valid": True, "sid": "sid-invalid-json"}}
        )

    app.router.add_post("/api/auth", authenticate)
    app.router.add_get("/api/queries", invalid_json)
    server = await aiohttp_client(app)
    api_config[CONF_PORT] = server.server.port
    async with aiohttp.ClientSession() as session:
        client = PiHoleClient(session, api_config)
        with pytest.raises(PiHoleConnectionError, match="JSON no válido"):
            await client.async_get_queries(length=1)


@pytest.mark.asyncio
async def test_self_signed_certificate_is_rejected(
    aiohttp_server, pi_hole_app, api_config
):
    ca = trustme.CA()
    cert = ca.issue_cert("127.0.0.1")
    server_ssl = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    cert.configure_cert(server_ssl)
    server = await aiohttp_server(pi_hole_app, ssl=server_ssl)
    api_config[CONF_HOST] = "127.0.0.1"
    api_config[CONF_PORT] = server.port
    api_config[CONF_USE_HTTPS] = True
    async with aiohttp.ClientSession() as session:
        client = PiHoleClient(session, api_config)
        with pytest.raises(PiHoleCertificateError):
            await client.async_authenticate()


@pytest.mark.asyncio
async def test_failed_request_and_client_close_release_resources(
    aiohttp_client, pi_hole_app, api_config, api_plan
):
    api_plan.query_responses.append((500, {"error": "synthetic failure"}))
    server = await aiohttp_client(pi_hole_app)
    api_config[CONF_PORT] = server.server.port
    session = aiohttp.ClientSession()
    client = PiHoleClient(session, api_config)

    with pytest.raises(PiHoleConnectionError):
        await client.async_get_queries(length=1)
    assert not session.closed  # PiHoleClient borrows Home Assistant's session.

    await client.async_close()
    assert api_plan.logout_sids == ["sid-1"]
    assert not session.closed
    await session.close()
    assert session.closed
