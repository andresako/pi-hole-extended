"""Shared, synthetic fixtures for integration tests."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import pytest
from aiohttp import web

from custom_components.pihole_queries.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USE_HTTPS,
    CONF_VERIFY_SSL,
)


TEST_PASSWORD = "synthetic-test-password"
QUERY_FIXTURE = [
    {
        "id": 1001,
        "timestamp": 1_735_689_600,
        "type": "A",
        "domain": "alpha.example.test",
        "client": "192.0.2.10",
        "status": "FORWARDED",
        "reply": "192.0.2.53",
    },
    {
        "id": 1002,
        "timestamp": 1_735_689_660,
        "type": "AAAA",
        "domain": "beta.example.test",
        "client": "2001:db8::10",
        "status": "GRAVITY",
        "reply": "",
    },
]


@dataclass
class ApiPlan:
    """Scripted responses for the local Pi-hole HTTP test server."""

    auth_responses: deque[tuple[int, dict[str, Any]]] = field(default_factory=deque)
    query_responses: deque[tuple[int, Any]] = field(default_factory=deque)
    auth_calls: int = 0
    query_calls: list[dict[str, str]] = field(default_factory=list)
    logout_sids: list[str] = field(default_factory=list)


@dataclass
class StoreDouble:
    """Minimal persistent-store double with injectable load/save failures."""

    value: Any = None
    load_error: Exception | None = None
    save_error: Exception | None = None
    saves: list[Any] = field(default_factory=list)

    async def async_load(self) -> Any:
        if self.load_error:
            raise self.load_error
        return self.value

    async def async_save(self, value: Any) -> None:
        if self.save_error:
            raise self.save_error
        self.value = value
        self.saves.append(value)


@dataclass
class LogbookDouble:
    """Append-only fake logbook that can fail on the next write."""

    entries: list[Any] = field(default_factory=list)
    write_error: Exception | None = None

    async def async_add_entry(self, entry: Any) -> None:
        if self.write_error:
            raise self.write_error
        self.entries.append(entry)


@pytest.fixture
def frozen_time(freezer):
    """Hold time at a stable instant for date-sensitive tests."""
    freezer.move_to("2025-01-01 00:00:00+00:00")
    return freezer


@pytest.fixture
def query_fixture() -> list[dict[str, Any]]:
    """Return synthetic records with stable IDs, times, and domains."""
    return [dict(record) for record in QUERY_FIXTURE]


@pytest.fixture
def api_plan() -> ApiPlan:
    """Return an empty response script for the local HTTP API double."""
    return ApiPlan()


@pytest.fixture
def store_double() -> StoreDouble:
    return StoreDouble()


@pytest.fixture
def logbook_double() -> LogbookDouble:
    return LogbookDouble()


@pytest.fixture
def api_config() -> dict[str, Any]:
    """Configuration for a synthetic local API endpoint."""
    return {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: 0,
        CONF_PASSWORD: TEST_PASSWORD,
        CONF_USE_HTTPS: False,
        CONF_VERIFY_SSL: True,
    }


@pytest.fixture
def pi_hole_app(api_plan: ApiPlan):
    """Create a controllable local Pi-hole v6-shaped API."""
    app = web.Application()

    async def authenticate(request: web.Request) -> web.Response:
        api_plan.auth_calls += 1
        body = await request.json()
        assert body == {"password": TEST_PASSWORD}
        status, payload = (
            api_plan.auth_responses.popleft()
            if api_plan.auth_responses
            else (200, {"session": {"valid": True, "sid": f"sid-{api_plan.auth_calls}"}})
        )
        return web.json_response(payload, status=status)

    async def queries(request: web.Request) -> web.Response:
        api_plan.query_calls.append(dict(request.headers))
        status, payload = (
            api_plan.query_responses.popleft()
            if api_plan.query_responses
            else (200, {"queries": QUERY_FIXTURE})
        )
        return web.json_response(payload, status=status)

    async def logout(request: web.Request) -> web.Response:
        api_plan.logout_sids.append(request.headers.get("X-FTL-SID", ""))
        return web.Response(status=204)

    app.router.add_post("/api/auth", authenticate)
    app.router.add_get("/api/queries", queries)
    app.router.add_delete("/api/auth", logout)
    return app
