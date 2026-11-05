"""Stable source/client identity and first-generation registry compatibility."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.pihole_queries import (
    _canonical_entity_id,
    _identity_storage_key,
    _IdentityStore,
    _make_store,
)
from custom_components.pihole_queries.const import (
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    DOMAIN,
    FIRST_SOURCE_GENERATION,
    STORAGE_VERSION,
)
from custom_components.pihole_queries.coordinator import PiHoleQueryCoordinator, _client_key
from custom_components.pihole_queries.sensor import (
    _client_device_identifier,
    _client_entity_unique_id,
)


class _Store:
    def __init__(self, value=None) -> None:
        self.value = deepcopy(value)
        self.saves: list[object] = []

    async def async_load(self):
        return deepcopy(self.value)

    async def async_save(self, value):
        self.value = deepcopy(value)
        self.saves.append(deepcopy(value))


def test_real_home_assistant_store_path_is_in_config_storage(tmp_path) -> None:
    config = SimpleNamespace(
        config_dir=str(tmp_path), path=lambda *parts: str(Path(tmp_path).joinpath(*parts))
    )
    store = _make_store(SimpleNamespace(config=config, data={}), "source", "first")
    assert Path(store.current.path) == tmp_path / ".storage" / "pihole_queries_cursor_source_first"
    assert Path(store.misplaced.path) == Path("/pihole_queries_translations")


class _EntityRegistry:
    def __init__(self, entries=()) -> None:
        self.entries = list(entries)

    def for_entry(self, entry_id):
        return [entity for entity in self.entries if entity.config_entry_id == entry_id]

    def async_get(self, entity_id):
        return next((entity for entity in self.entries if entity.entity_id == entity_id), None)

    def async_update_entity(self, entity_id, **changes):
        entity = self.async_get(entity_id)
        for key, value in changes.items():
            setattr(entity, key.removeprefix("new_"), value)
        return entity

    def async_remove(self, entity_id):
        self.entries = [entity for entity in self.entries if entity.entity_id != entity_id]


class _DeviceRegistry:
    def async_get(self, _device_id):
        return None


def _coordinator_for_store(store):
    coordinator = object.__new__(PiHoleQueryCoordinator)
    coordinator.store = store
    coordinator.cursor = None
    coordinator.client_data = {}
    coordinator._bootstrapped = False
    coordinator.coverage = "unknown"
    coordinator.coverage_reason = "no_baseline"
    coordinator.last_query_time = None
    coordinator.awaiting_generation = False
    coordinator.recovery_cursor = None
    coordinator._client_store_invalid = False
    coordinator._store_state_invalid = False
    return coordinator


@pytest.mark.asyncio
async def test_misplaced_store_is_recovered_once_for_matching_source() -> None:
    source = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    payload = {
        "schema_version": STORAGE_VERSION,
        "source_id": source,
        "source_generation": FIRST_SOURCE_GENERATION,
        "data": {"cursor": 500, "clients": {"client": {"query_count": 42}}},
    }
    current, misplaced = _Store(), _Store(payload)
    store = _IdentityStore(current, source, FIRST_SOURCE_GENERATION, misplaced=misplaced)

    recovered = await store.async_load()
    assert recovered["clients"]["client"]["query_count"] == 42
    assert current.value["data"] == recovered
    assert misplaced.value == payload

    # Once copied, later updates in the correct location win over the old file.
    current.value["data"]["clients"]["client"]["query_count"] = 100
    assert (await store.async_load())["clients"]["client"]["query_count"] == 100


@pytest.mark.asyncio
async def test_misplaced_store_from_other_source_is_never_imported() -> None:
    current = _Store()
    misplaced = _Store({"source_id": "other", "source_generation": FIRST_SOURCE_GENERATION})
    store = _IdentityStore(current, "source", FIRST_SOURCE_GENERATION, misplaced=misplaced)
    assert await store.async_load() is None
    assert not current.saves


@pytest.mark.asyncio
async def test_misplaced_store_is_not_used_if_current_store_exists() -> None:
    source = "source"
    payload = {
        "schema_version": STORAGE_VERSION,
        "source_id": source,
        "source_generation": FIRST_SOURCE_GENERATION,
        "data": {"cursor": 1},
    }
    current, misplaced = _Store(payload), _Store({"source_id": source})
    store = _IdentityStore(current, source, FIRST_SOURCE_GENERATION, misplaced=misplaced)
    assert (await store.async_load())["cursor"] == 1
    assert not current.saves


@pytest.mark.asyncio
async def test_store_with_different_identity_is_rejected() -> None:
    current = _Store(
        {
            "schema_version": STORAGE_VERSION,
            "source_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "source_generation": FIRST_SOURCE_GENERATION,
            "data": {"cursor": 500},
        }
    )
    store = _IdentityStore(current, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", FIRST_SOURCE_GENERATION)

    with pytest.raises(HomeAssistantError, match="does not match"):
        await store.async_load()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, True, 7, "bad", []])
async def test_store_with_non_mapping_payload_is_rejected(payload) -> None:
    current = _Store(
        {
            "schema_version": STORAGE_VERSION,
            "source_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "source_generation": FIRST_SOURCE_GENERATION,
            "data": payload,
        }
    )
    store = _IdentityStore(
        current,
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        FIRST_SOURCE_GENERATION,
    )

    with pytest.raises(HomeAssistantError, match="schema"):
        await store.async_load()


@pytest.mark.asyncio
@pytest.mark.parametrize("schema_version", [True, 2.0, "2"])
async def test_store_requires_integer_schema_version(schema_version) -> None:
    current = _Store(
        {
            "schema_version": schema_version,
            "source_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "source_generation": FIRST_SOURCE_GENERATION,
            "data": {},
        }
    )
    store = _IdentityStore(
        current,
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        FIRST_SOURCE_GENERATION,
    )

    with pytest.raises(HomeAssistantError, match="schema"):
        await store.async_load()


@pytest.mark.asyncio
async def test_malformed_client_fields_are_excluded_and_coverage_is_unknown() -> None:
    data = {
        "cursor": 500,
        "clients": {
            "192.0.2.10": {
                "query_count": 42,
                "allowed_count": True,
                "blocked_count": 1.7,
                "class_rates": "not-a-map",
            }
        },
    }
    current = _Store(
        {
            "schema_version": STORAGE_VERSION,
            "source_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "source_generation": FIRST_SOURCE_GENERATION,
            "data": data,
        }
    )
    store = _IdentityStore(
        current,
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        FIRST_SOURCE_GENERATION,
    )

    prepared = await store.async_load()
    coordinator = _coordinator_for_store(store)
    await coordinator.async_load_cursor()

    summary = coordinator.client_data["192.0.2.10"]
    assert summary["query_count"] == 42
    assert "allowed_count" not in summary
    assert "blocked_count" not in summary
    assert summary["class_rates"] == {"allowed": 0, "blocked": 0, "provisional": 0}
    assert prepared["coverage"] == "unknown"
    assert prepared["coverage_reason"] == "invalid_store_state"
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "invalid_store_state"
    assert current.value["data"] == prepared
    assert "allowed_count" not in current.value["data"]["clients"]["192.0.2.10"]


def test_first_generation_ids_use_the_source_and_keep_entity_ids_short() -> None:
    entry = SimpleNamespace(
        entry_id="entry-a",
        unique_id="https://pihole.example.test:443",
        data={
            CONF_SOURCE_ID: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            CONF_SOURCE_GENERATION: FIRST_SOURCE_GENERATION,
        },
    )
    prefix = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa_first_192.0.2.10"

    assert _client_entity_unique_id(entry, "192.0.2.10", "queries") == prefix
    assert _client_entity_unique_id(entry, "192.0.2.10", "allowed") == f"{prefix}_allowed"
    assert _client_device_identifier(entry, "192.0.2.10") == (DOMAIN, prefix)
    assert (
        _canonical_entity_id(entry, SimpleNamespace(domain="sensor", unique_id=prefix))
        == "sensor.phe_192_0_2_10_dns_queries"
    )


def test_replacement_generation_scopes_entity_ids_and_devices() -> None:
    source_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    generation = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    entry = SimpleNamespace(
        entry_id="entry-a",
        unique_id="https://old-host.example.test:443",
        data={CONF_SOURCE_ID: source_id, CONF_SOURCE_GENERATION: generation},
    )

    expected = f"{source_id}_{generation}_192.0.2.10"
    assert _client_entity_unique_id(entry, "192.0.2.10", "queries") == expected
    assert _client_device_identifier(entry, "192.0.2.10") == (DOMAIN, expected)
    entity = SimpleNamespace(
        domain="sensor",
        unique_id=f"{expected}_allowed",
    )
    assert _canonical_entity_id(entry, entity) == (
        f"sensor.phe_{generation.replace('-', '_')}_192_0_2_10_allowed"
    )


@pytest.mark.parametrize(
    ("classification", "localized"),
    [
        ("allowed", "allowed"),
        ("blocked", "blocked"),
        ("provisional", "provisional"),
    ],
)
def test_class_entities_get_their_metric_in_canonical_id(
    classification: str, localized: str
) -> None:
    generation = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    entry = SimpleNamespace(
        entry_id="entry-a",
        unique_id="https://old-host.example.test:443",
        data={
            CONF_SOURCE_ID: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            CONF_SOURCE_GENERATION: generation,
        },
    )
    source_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    entity = SimpleNamespace(
        domain="sensor",
        unique_id=(f"{source_id}_{generation}_192.0.2.10_{classification}"),
    )

    assert _canonical_entity_id(entry, entity) == (
        f"sensor.phe_{generation.replace('-', '_')}_192_0_2_10_{localized}"
    )


def test_two_sources_with_same_client_ip_have_distinct_entity_and_device_ids() -> None:
    entries = [
        SimpleNamespace(
            entry_id="entry-a",
            unique_id="http://one.example.test:80",
            data={
                CONF_SOURCE_ID: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                CONF_SOURCE_GENERATION: FIRST_SOURCE_GENERATION,
            },
        ),
        SimpleNamespace(
            entry_id="entry-b",
            unique_id="http://two.example.test:80",
            data={
                CONF_SOURCE_ID: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                CONF_SOURCE_GENERATION: FIRST_SOURCE_GENERATION,
            },
        ),
    ]
    entity_ids = {_client_entity_unique_id(entry, "192.0.2.10", "queries") for entry in entries}
    device_ids = {_client_device_identifier(entry, "192.0.2.10") for entry in entries}

    assert len(entity_ids) == 2
    assert len(device_ids) == 2


def test_client_name_fallback_is_normalized_without_changing_display_name() -> None:
    first = _client_key({"client": {"name": "Living Room"}})
    second = _client_key({"client": {"name": "  living   room  "}})

    assert first[0] == second[0] == "living room"
    assert first[1] == "Living Room"
    assert second[1] == "living   room"


def test_same_ip_registry_ids_survive_reverse_reload_order() -> None:
    entries = [
        SimpleNamespace(
            entry_id="entry-a",
            unique_id="http://one.example.test:80",
            data={
                CONF_SOURCE_ID: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                CONF_SOURCE_GENERATION: FIRST_SOURCE_GENERATION,
            },
        ),
        SimpleNamespace(
            entry_id="entry-b",
            unique_id="http://two.example.test:80",
            data={
                CONF_SOURCE_ID: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                CONF_SOURCE_GENERATION: FIRST_SOURCE_GENERATION,
            },
        ),
    ]
    registry: dict[str, str] = {}
    for entry in entries:
        unique_id = _client_entity_unique_id(entry, "192.0.2.10", "queries")
        entity = SimpleNamespace(domain="sensor", unique_id=unique_id)
        desired = _canonical_entity_id(entry, entity)
        assert desired is not None
        entity_id = desired
        suffix = 2
        while entity_id in registry.values():
            entity_id = f"{desired}_{suffix}"
            suffix += 1
        registry[unique_id] = entity_id
    before = dict(registry)

    # Reload in the opposite order: existing HA unique IDs resolve to their
    # registered entity IDs; startup does not canonicalize them again.
    for entry in reversed(entries):
        unique_id = _client_entity_unique_id(entry, "192.0.2.10", "queries")
        assert registry[unique_id] == before[unique_id]
    assert len(set(registry.values())) == 2
    assert (
        len(
            {
                _identity_storage_key(
                    entry.data[CONF_SOURCE_ID], entry.data[CONF_SOURCE_GENERATION]
                )
                for entry in entries
            }
        )
        == 2
    )


@pytest.mark.asyncio
async def test_same_ip_sources_keep_independent_store_payloads() -> None:
    identities = [
        ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", FIRST_SOURCE_GENERATION),
        ("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", FIRST_SOURCE_GENERATION),
    ]
    stores = [
        _IdentityStore(
            _Store(
                {
                    "schema_version": STORAGE_VERSION,
                    "source_id": source_id,
                    "source_generation": generation,
                    "data": {
                        "cursor": cursor,
                        "clients": {"192.0.2.10": {"query_count": count}},
                    },
                }
            ),
            source_id,
            generation,
        )
        for (source_id, generation), cursor, count in zip(
            identities, (100, 900), (5, 9), strict=True
        )
    ]

    loaded = [await store.async_load() for store in stores]

    assert [data["cursor"] for data in loaded] == [100, 900]
    assert [data["clients"]["192.0.2.10"]["query_count"] for data in loaded] == [5, 9]
    assert len({_identity_storage_key(*identity) for identity in identities}) == 2
