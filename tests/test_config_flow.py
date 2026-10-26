"""Connection parsing and source-safe Home Assistant reconfiguration."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
import voluptuous as vol

from custom_components.pihole_queries import config_flow
from custom_components.pihole_queries.api import endpoint_unique_id, normalize_host
from custom_components.pihole_queries.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_RECONFIGURE_SOURCE_ACTION,
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    CONF_USE_HTTPS,
    CONF_VERIFY_SSL,
    DOMAIN,
    LEGACY_SOURCE_GENERATION,
    RECONFIGURE_NEW_SOURCE,
    RECONFIGURE_SAME_SOURCE,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" pihole.Example.Test. ", "pihole.example.test"),
        ("192.0.2.10", "192.0.2.10"),
        ("2001:0db8::10", "2001:db8::10"),
        ("[2001:db8::10]", "2001:db8::10"),
        ("pi.holé.example", "pi.xn--hol-dma.example"),
    ],
)
def test_host_is_canonicalized_as_a_bare_hostname_or_ip(value: str, expected: str) -> None:
    assert normalize_host(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://pi.hole",
        "pi.hole/api",
        "pi.hole:8080",
        "user@pi.hole",
        "fe80::1%eth0",
        "host.example.test..",
        "[pihole.example.test]",
        "[192.0.2.10]",
        "[2001:db8::10",
        "2001:db8::invalid",
        "999.1.2.3",
        "-bad.example.test",
    ],
)
def test_host_rejects_urls_paths_ports_and_invalid_addresses(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_host(value)


def test_endpoint_key_brackets_ipv6_and_keeps_explicit_port() -> None:
    assert endpoint_unique_id("2001:db8::10", 8443, True) == "https://[2001:db8::10]:8443"
    assert endpoint_unique_id("PiHole.Example.Test", 8080, False) == "http://pihole.example.test:8080"


@pytest.mark.asyncio
async def test_client_builds_https_url_with_ipv6_and_custom_port(api_config: dict) -> None:
    import aiohttp

    from custom_components.pihole_queries.api import PiHoleClient

    async with aiohttp.ClientSession() as session:
        client = PiHoleClient(
            session,
            {
                **api_config,
                CONF_HOST: "2001:db8::10",
                CONF_PORT: 8443,
                CONF_USE_HTTPS: True,
            },
        )
        assert client._base_url == "https://[2001:db8::10]:8443"


@pytest.mark.parametrize("host", ["https://pi.hole", "pi.hole/api", "pi.hole:8080"])
@pytest.mark.asyncio
async def test_client_rejects_ambiguous_host_values(host: str, api_config: dict) -> None:
    import aiohttp

    from custom_components.pihole_queries.api import PiHoleClient

    async with aiohttp.ClientSession() as session:
        with pytest.raises(ValueError):
            PiHoleClient(session, {**api_config, CONF_HOST: host})


def test_reconfigure_schema_requires_an_explicit_server_identity_action() -> None:
    schema = config_flow._reconfigure_schema(
        {CONF_HOST: "pi.hole", CONF_PORT: 8443, CONF_USE_HTTPS: True}
    )
    valid = {
        CONF_HOST: "pi.hole",
        CONF_PORT: 8443,
        CONF_USE_HTTPS: True,
        CONF_VERIFY_SSL: True,
        CONF_RECONFIGURE_SOURCE_ACTION: RECONFIGURE_SAME_SOURCE,
    }
    assert schema(valid)[CONF_PORT] == 8443
    with pytest.raises(vol.Invalid):
        schema({key: value for key, value in valid.items() if key != CONF_RECONFIGURE_SOURCE_ACTION})


def test_config_translation_files_have_matching_keys() -> None:
    root = Path(__file__).parents[1] / "custom_components" / DOMAIN
    files = [root / "strings.json", root / "translations" / "en.json", root / "translations" / "es.json"]

    def keys(value):
        if isinstance(value, dict):
            return {key: keys(item) for key, item in value.items()}
        return None

    translations = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    assert keys(translations[0]) == keys(translations[1]) == keys(translations[2])


class _Entries:
    def __init__(self, entries: list[SimpleNamespace]) -> None:
        self.entries = entries

    def async_get_entry(self, entry_id: str):
        return next((entry for entry in self.entries if entry.entry_id == entry_id), None)

    def async_entries(self, _domain: str):
        return self.entries


def _entry(*, generation: str = LEGACY_SOURCE_GENERATION) -> SimpleNamespace:
    return SimpleNamespace(
        entry_id="entry-1",
        unique_id="http://old.example.test:80",
        title="Pi-hole · old.example.test",
        data={
            CONF_HOST: "old.example.test",
            CONF_PORT: 80,
            CONF_USE_HTTPS: False,
            CONF_VERIFY_SSL: True,
            CONF_PASSWORD: "old-secret-fixture",
            CONF_SOURCE_ID: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            CONF_SOURCE_GENERATION: generation,
        },
    )


def _reconfigure_flow(entry: SimpleNamespace, monkeypatch):
    flow = config_flow.PiHoleQueriesConfigFlow()
    flow.hass = SimpleNamespace(config_entries=_Entries([entry]))
    flow.context = {"entry_id": entry.entry_id}
    monkeypatch.setattr(
        flow,
        "async_show_form",
        lambda **kwargs: {"type": "form", **kwargs},
    )
    updates: list[dict] = []
    monkeypatch.setattr(
        flow,
        "async_update_reload_and_abort",
        lambda target, **kwargs: updates.append({"entry": target, **kwargs}) or {"type": "abort"},
    )
    return flow, updates


@pytest.mark.asyncio
async def test_user_flow_rejects_full_urls_before_connection_validation(monkeypatch) -> None:
    flow = config_flow.PiHoleQueriesConfigFlow()
    flow.hass = SimpleNamespace()
    monkeypatch.setattr(
        flow,
        "async_show_form",
        lambda **kwargs: {"type": "form", **kwargs},
    )
    validated: list[bool] = []

    async def validate(_hass, _data):
        validated.append(True)

    monkeypatch.setattr(config_flow, "_validate_connection", validate)
    result = await flow.async_step_user(
        {
            CONF_HOST: "https://pihole.example.test/api",
            CONF_PORT: 443,
            CONF_USE_HTTPS: True,
            CONF_VERIFY_SSL: True,
            CONF_PASSWORD: "synthetic-secret-fixture",
            "scan_interval": 15,
        }
    )

    assert result["errors"]["base"] == "invalid_host"
    assert validated == []


@pytest.mark.asyncio
async def test_user_flow_canonicalizes_ipv6_and_keeps_explicit_https_port(monkeypatch) -> None:
    flow = config_flow.PiHoleQueriesConfigFlow()
    flow.hass = SimpleNamespace(config_entries=_Entries([]))
    captured: dict = {}

    async def set_unique_id(unique_id):
        captured["unique_id"] = unique_id

    monkeypatch.setattr(flow, "async_set_unique_id", set_unique_id)
    monkeypatch.setattr(flow, "_abort_if_unique_id_configured", lambda: None)
    monkeypatch.setattr(
        flow,
        "async_create_entry",
        lambda **kwargs: captured.update(kwargs) or {"type": "create_entry", **kwargs},
    )

    async def validate(_hass, data):
        captured["validated"] = data.copy()

    monkeypatch.setattr(config_flow, "_validate_connection", validate)
    result = await flow.async_step_user(
        {
            CONF_HOST: "2001:0db8::10",
            CONF_PORT: 8443,
            CONF_USE_HTTPS: True,
            CONF_VERIFY_SSL: True,
            CONF_PASSWORD: "synthetic-secret-fixture",
            "scan_interval": 15,
        }
    )

    assert result["type"] == "create_entry"
    assert captured["unique_id"] == "https://[2001:db8::10]:8443"
    assert captured["data"][CONF_HOST] == "2001:db8::10"
    assert captured["data"][CONF_PORT] == 8443
    assert captured["data"][CONF_SOURCE_GENERATION] == LEGACY_SOURCE_GENERATION
    assert str(UUID(captured["data"][CONF_SOURCE_ID])) == captured["data"][CONF_SOURCE_ID]


@pytest.mark.asyncio
async def test_user_flow_can_readd_endpoint_released_by_reconfigure(monkeypatch) -> None:
    released_endpoint_owner = _entry()
    released_endpoint_owner.data[CONF_HOST] = "new.example.test"
    flow = config_flow.PiHoleQueriesConfigFlow()
    flow.hass = SimpleNamespace(config_entries=_Entries([released_endpoint_owner]))
    captured: dict = {}

    async def set_unique_id(unique_id):
        captured.setdefault("unique_ids", []).append(unique_id)
        if unique_id == "http://old.example.test:80":
            return released_endpoint_owner
        return None

    monkeypatch.setattr(flow, "async_set_unique_id", set_unique_id)
    monkeypatch.setattr(flow, "_abort_if_unique_id_configured", lambda: None)
    monkeypatch.setattr(
        flow,
        "async_create_entry",
        lambda **kwargs: captured.update(kwargs) or {"type": "create_entry", **kwargs},
    )

    async def validate(_hass, _data):
        return None

    monkeypatch.setattr(config_flow, "_validate_connection", validate)
    result = await flow.async_step_user(
        {
            CONF_HOST: "old.example.test",
            CONF_PORT: 80,
            CONF_USE_HTTPS: False,
            CONF_VERIFY_SSL: True,
            CONF_PASSWORD: "synthetic-secret-fixture",
            "scan_interval": 15,
        }
    )

    assert result["type"] == "create_entry"
    assert captured["unique_ids"][0] == "http://old.example.test:80"
    assert captured["unique_ids"][1] == f"source:{captured['data'][CONF_SOURCE_ID]}"
    assert captured["data"][CONF_HOST] == "old.example.test"


@pytest.mark.asyncio
async def test_concurrent_user_flows_cannot_add_released_endpoint_twice(monkeypatch) -> None:
    released_endpoint_owner = _entry()
    released_endpoint_owner.data[CONF_HOST] = "new.example.test"
    entries = _Entries([released_endpoint_owner])
    flows = [config_flow.PiHoleQueriesConfigFlow() for _ in range(2)]
    created: list[dict] = []
    validations_entered = 0
    both_validations_entered = asyncio.Event()
    release_validations = asyncio.Event()

    async def validate(_hass, _data):
        nonlocal validations_entered
        validations_entered += 1
        if validations_entered == len(flows):
            both_validations_entered.set()
        await release_validations.wait()

    monkeypatch.setattr(config_flow, "_validate_connection", validate)

    for flow in flows:
        flow.hass = SimpleNamespace(config_entries=entries)
        monkeypatch.setattr(
            flow,
            "async_show_form",
            lambda **kwargs: {"type": "form", **kwargs},
        )

        async def set_unique_id(unique_id):
            if unique_id == "http://old.example.test:80":
                return released_endpoint_owner
            return None

        monkeypatch.setattr(flow, "async_set_unique_id", set_unique_id)
        monkeypatch.setattr(flow, "_abort_if_unique_id_configured", lambda: None)

        def create_entry(**kwargs):
            created.append(kwargs)
            entries.entries.append(
                SimpleNamespace(
                    entry_id=f"new-{len(created)}",
                    data=kwargs["data"],
                )
            )
            return {"type": "create_entry", **kwargs}

        monkeypatch.setattr(flow, "async_create_entry", create_entry)

    user_input = {
        CONF_HOST: "old.example.test",
        CONF_PORT: 80,
        CONF_USE_HTTPS: False,
        CONF_VERIFY_SSL: True,
        CONF_PASSWORD: "synthetic-secret-fixture",
        "scan_interval": 15,
    }
    tasks = [asyncio.create_task(flow.async_step_user(user_input)) for flow in flows]
    await asyncio.wait_for(both_validations_entered.wait(), timeout=1)
    release_validations.set()
    results = await asyncio.gather(*tasks)

    assert len(created) == 1
    assert sum(result["type"] == "create_entry" for result in results) == 1
    assert sum(result.get("errors", {}).get("base") == "already_configured" for result in results) == 1


@pytest.mark.asyncio
async def test_user_flow_detects_endpoint_duplicate_even_with_legacy_ipv6_unique_id(
    monkeypatch,
) -> None:
    legacy_entry = SimpleNamespace(
        entry_id="entry-legacy-ipv6",
        unique_id="https://2001:db8::10:8443",
        data={
            CONF_HOST: "2001:db8::10",
            CONF_PORT: 8443,
            CONF_USE_HTTPS: True,
        },
    )
    flow = config_flow.PiHoleQueriesConfigFlow()
    flow.hass = SimpleNamespace(config_entries=_Entries([legacy_entry]))
    monkeypatch.setattr(
        flow,
        "async_show_form",
        lambda **kwargs: {"type": "form", **kwargs},
    )
    validated: list[bool] = []

    async def validate(_hass, _data):
        validated.append(True)

    monkeypatch.setattr(config_flow, "_validate_connection", validate)
    result = await flow.async_step_user(
        {
            CONF_HOST: "2001:0db8::10",
            CONF_PORT: 8443,
            CONF_USE_HTTPS: True,
            CONF_VERIFY_SSL: True,
            CONF_PASSWORD: "synthetic-secret-fixture",
            "scan_interval": 15,
        }
    )

    assert result["errors"]["base"] == "already_configured"
    assert validated == []


@pytest.mark.asyncio
async def test_same_pi_hole_reconfigure_preserves_identity_and_blank_password(
    monkeypatch,
) -> None:
    entry = _entry()
    flow, updates = _reconfigure_flow(entry, monkeypatch)
    validated: list[dict] = []

    async def validate(_hass, data):
        validated.append(data)

    monkeypatch.setattr(config_flow, "_validate_connection", validate)
    result = await flow.async_step_reconfigure(
        {
            CONF_HOST: " PI.HOLE ",
            CONF_PORT: 8443,
            CONF_USE_HTTPS: True,
            CONF_VERIFY_SSL: True,
            CONF_PASSWORD: "",
            CONF_RECONFIGURE_SOURCE_ACTION: RECONFIGURE_SAME_SOURCE,
        }
    )

    assert result["type"] == "abort"
    assert len(updates) == 1
    saved = updates[0]["data"]
    assert saved[CONF_HOST] == "pi.hole"
    assert saved[CONF_PORT] == 8443
    assert saved[CONF_USE_HTTPS] is True
    assert saved[CONF_PASSWORD] == "old-secret-fixture"
    assert saved[CONF_SOURCE_ID] == entry.data[CONF_SOURCE_ID]
    assert saved[CONF_SOURCE_GENERATION] == LEGACY_SOURCE_GENERATION
    assert updates[0]["entry"].unique_id == "http://old.example.test:80"
    assert validated[0][CONF_PORT] == 8443


@pytest.mark.asyncio
async def test_replacement_reconfigure_creates_generation_and_archives_old_entities(
    monkeypatch,
) -> None:
    entry = _entry(generation="legacy")
    flow, updates = _reconfigure_flow(entry, monkeypatch)
    archived: list[SimpleNamespace] = []

    async def validate(_hass, _data):
        return None

    monkeypatch.setattr(config_flow, "_validate_connection", validate)
    monkeypatch.setattr(
        config_flow, "_archive_client_entities", lambda hass, current: archived.append(current)
    )
    result = await flow.async_step_reconfigure(
        {
            CONF_HOST: "new.example.test",
            CONF_PORT: 80,
            CONF_USE_HTTPS: False,
            CONF_VERIFY_SSL: True,
            CONF_PASSWORD: "new-secret-fixture",
            CONF_RECONFIGURE_SOURCE_ACTION: RECONFIGURE_NEW_SOURCE,
        }
    )

    assert result["type"] == "abort"
    assert archived == [entry]
    saved = updates[0]["data"]
    assert saved[CONF_SOURCE_ID] == entry.data[CONF_SOURCE_ID]
    assert saved[CONF_SOURCE_GENERATION] != "legacy"
    assert str(UUID(saved[CONF_SOURCE_GENERATION])) == saved[CONF_SOURCE_GENERATION]
    assert saved[CONF_PASSWORD] == "new-secret-fixture"


@pytest.mark.asyncio
async def test_invalid_endpoint_or_duplicate_is_not_validated_or_saved(monkeypatch) -> None:
    entry = _entry()
    flow, updates = _reconfigure_flow(entry, monkeypatch)
    validations: list[bool] = []

    async def validate(_hass, _data):
        validations.append(True)

    monkeypatch.setattr(config_flow, "_validate_connection", validate)
    invalid = await flow.async_step_reconfigure(
        {
            CONF_HOST: "https://other.example.test/path",
            CONF_PORT: 443,
            CONF_USE_HTTPS: True,
            CONF_VERIFY_SSL: True,
            CONF_RECONFIGURE_SOURCE_ACTION: RECONFIGURE_SAME_SOURCE,
        }
    )
    assert invalid["errors"]["base"] == "invalid_host"

    duplicate = SimpleNamespace(
        entry_id="entry-2",
        data={
            CONF_HOST: "new.example.test",
            CONF_PORT: 443,
            CONF_USE_HTTPS: True,
        },
    )
    flow.hass.config_entries.entries.append(duplicate)
    repeated = await flow.async_step_reconfigure(
        {
            CONF_HOST: "NEW.EXAMPLE.TEST.",
            CONF_PORT: 443,
            CONF_USE_HTTPS: True,
            CONF_VERIFY_SSL: True,
            CONF_RECONFIGURE_SOURCE_ACTION: RECONFIGURE_SAME_SOURCE,
        }
    )
    assert repeated["errors"]["base"] == "already_configured"
    assert validations == []
    assert updates == []


@pytest.mark.asyncio
async def test_concurrent_reconfigure_flows_cannot_claim_same_endpoint(monkeypatch) -> None:
    first_entry = _entry()
    first_entry.unique_id = "historical-first"
    first_entry.data[CONF_HOST] = "first.example.test"
    first_entry.entry_id = "entry-first"
    second_entry = _entry()
    second_entry.unique_id = "historical-second"
    second_entry.data[CONF_HOST] = "second.example.test"
    second_entry.entry_id = "entry-second"
    entries = _Entries([first_entry, second_entry])
    flows = [config_flow.PiHoleQueriesConfigFlow() for _ in range(2)]
    updates: list[SimpleNamespace] = []
    validations_entered = 0
    both_validations_entered = asyncio.Event()
    release_validations = asyncio.Event()

    async def validate(_hass, _data):
        nonlocal validations_entered
        validations_entered += 1
        if validations_entered == len(flows):
            both_validations_entered.set()
        await release_validations.wait()

    monkeypatch.setattr(config_flow, "_validate_connection", validate)

    for flow, entry in zip(flows, entries.entries):
        flow.hass = SimpleNamespace(config_entries=entries)
        flow.context = {"entry_id": entry.entry_id}
        monkeypatch.setattr(
            flow,
            "async_show_form",
            lambda **kwargs: {"type": "form", **kwargs},
        )

        def update_entry(target, **kwargs):
            target.data = kwargs["data"]
            updates.append(target)
            return {"type": "abort", "reason": kwargs["reason"]}

        monkeypatch.setattr(flow, "async_update_reload_and_abort", update_entry)

    user_input = {
        CONF_HOST: "shared.example.test",
        CONF_PORT: 80,
        CONF_USE_HTTPS: False,
        CONF_VERIFY_SSL: True,
        CONF_RECONFIGURE_SOURCE_ACTION: RECONFIGURE_SAME_SOURCE,
    }
    tasks = [asyncio.create_task(flow.async_step_reconfigure(user_input)) for flow in flows]
    await asyncio.wait_for(both_validations_entered.wait(), timeout=1)
    release_validations.set()
    results = await asyncio.gather(*tasks)

    assert len(updates) == 1
    assert sum(result["type"] == "abort" for result in results) == 1
    assert sum(result.get("errors", {}).get("base") == "already_configured" for result in results) == 1


@pytest.mark.asyncio
async def test_concurrent_reconfigure_of_same_entry_cannot_restore_stale_generation(
    monkeypatch,
) -> None:
    entry = _entry(generation=LEGACY_SOURCE_GENERATION)
    entries = _Entries([entry])
    flows = [config_flow.PiHoleQueriesConfigFlow() for _ in range(2)]
    updates: list[dict] = []
    archived: list[SimpleNamespace] = []
    validations_entered = 0
    both_validations_entered = asyncio.Event()
    release_validations = asyncio.Event()

    async def validate(_hass, _data):
        nonlocal validations_entered
        validations_entered += 1
        if validations_entered == len(flows):
            both_validations_entered.set()
        await release_validations.wait()

    monkeypatch.setattr(config_flow, "_validate_connection", validate)
    monkeypatch.setattr(
        config_flow,
        "_archive_client_entities",
        lambda hass, current: archived.append(current),
    )

    for flow in flows:
        flow.hass = SimpleNamespace(config_entries=entries)
        flow.context = {"entry_id": entry.entry_id}
        monkeypatch.setattr(
            flow,
            "async_show_form",
            lambda **kwargs: {"type": "form", **kwargs},
        )

        def update_entry(target, **kwargs):
            target.data = kwargs["data"]
            updates.append(kwargs)
            return {"type": "abort", "reason": kwargs["reason"]}

        monkeypatch.setattr(flow, "async_update_reload_and_abort", update_entry)

    base_input = {
        CONF_HOST: "replacement.example.test",
        CONF_PORT: 80,
        CONF_USE_HTTPS: False,
        CONF_VERIFY_SSL: True,
    }
    tasks = [
        asyncio.create_task(
            flows[0].async_step_reconfigure(
                {**base_input, CONF_RECONFIGURE_SOURCE_ACTION: RECONFIGURE_NEW_SOURCE}
            )
        ),
        asyncio.create_task(
            flows[1].async_step_reconfigure(
                {**base_input, CONF_RECONFIGURE_SOURCE_ACTION: RECONFIGURE_SAME_SOURCE}
            )
        ),
    ]
    await asyncio.wait_for(both_validations_entered.wait(), timeout=1)
    release_validations.set()
    results = await asyncio.gather(*tasks)

    assert len(updates) == 1
    assert updates[0]["data"][CONF_SOURCE_GENERATION] != LEGACY_SOURCE_GENERATION
    assert archived == [entry]
    assert sum(result["type"] == "abort" for result in results) == 2
    assert sum(result.get("reason") == "reconfigure_entry_changed" for result in results) == 1


@pytest.mark.asyncio
async def test_connection_error_does_not_update_entry(monkeypatch) -> None:
    entry = _entry()
    flow, updates = _reconfigure_flow(entry, monkeypatch)

    async def reject(_hass, _data):
        raise config_flow.PiHoleAuthenticationError

    monkeypatch.setattr(config_flow, "_validate_connection", reject)
    result = await flow.async_step_reconfigure(
        {
            CONF_HOST: "new.example.test",
            CONF_PORT: 80,
            CONF_USE_HTTPS: False,
            CONF_VERIFY_SSL: True,
            CONF_RECONFIGURE_SOURCE_ACTION: RECONFIGURE_SAME_SOURCE,
        }
    )

    assert result["errors"]["base"] == "invalid_auth"
    assert updates == []
    assert entry.data[CONF_HOST] == "old.example.test"


@pytest.mark.asyncio
async def test_connection_validation_closes_authenticated_sid_after_query_failure(
    aiohttp_client, pi_hole_app, api_plan, api_config, monkeypatch
) -> None:
    import aiohttp

    api_plan.query_responses.append((500, {"error": "synthetic failure"}))
    server = await aiohttp_client(pi_hole_app)
    api_config[CONF_HOST] = "127.0.0.1"
    api_config[CONF_PORT] = server.server.port
    session = aiohttp.ClientSession()
    monkeypatch.setattr(config_flow, "async_get_clientsession", lambda _hass: session)

    with pytest.raises(config_flow.PiHoleConnectionError) as error:
        await config_flow._validate_connection(SimpleNamespace(), api_config)

    assert "synthetic-test-password" not in str(error.value)
    assert "sid-1" not in str(error.value)
    assert api_plan.logout_sids == ["sid-1"]
    assert not session.closed
    await session.close()


def test_archive_helper_disables_only_current_generation_client_sensors(monkeypatch) -> None:
    from homeassistant.helpers.entity_registry import RegistryEntryDisabler

    entry = _entry(generation="legacy")
    entities = [
        SimpleNamespace(
            entity_id="sensor.old_client",
            unique_id="http://old.example.test:80_192.0.2.10",
            platform=DOMAIN,
            domain="sensor",
        ),
        SimpleNamespace(
            entity_id="sensor.user_disabled",
            unique_id="http://old.example.test:80_192.0.2.12",
            platform=DOMAIN,
            domain="sensor",
            disabled_by=RegistryEntryDisabler.USER,
        ),
        SimpleNamespace(
            entity_id="sensor.diagnostic",
            unique_id="entry-1_communication_status",
            platform=DOMAIN,
            domain="sensor",
        ),
        SimpleNamespace(
            entity_id="sensor.other",
            unique_id="http://old.example.test:80_192.0.2.11",
            platform="other",
            domain="sensor",
        ),
    ]

    class Registry:
        def __init__(self):
            self.updated: list[tuple[str, dict]] = []

        def async_update_entity(self, entity_id, **changes):
            self.updated.append((entity_id, changes))

    registry = Registry()
    monkeypatch.setattr(config_flow.er, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        config_flow.er,
        "async_entries_for_config_entry",
        lambda _registry, _entry_id: entities,
    )

    config_flow._archive_client_entities(SimpleNamespace(), entry)

    assert registry.updated == [
        (
            "sensor.old_client",
            {"disabled_by": RegistryEntryDisabler.INTEGRATION},
        )
    ]
