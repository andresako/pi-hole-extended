"""Stable source/client identity and first-generation registry compatibility."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as device_registry_module
from homeassistant.helpers import entity_registry as entity_registry_module

from custom_components.pihole_queries import (
    _IdentityStore,
    _canonical_entity_id,
    _identity_storage_key,
    async_migrate_entry,
)
from custom_components.pihole_queries.const import (
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    DOMAIN,
    LEGACY_SOURCE_GENERATION,
    LEGACY_STORAGE_VERSION,
    STORAGE_KEY_PREFIX,
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


def _migration_hass(entry, monkeypatch, entities=()):
    registry = _EntityRegistry(entities)

    def update_entry(target, *, data=None, version=None):
        if data is not None:
            target.data = data
        if version is not None:
            target.version = version

    hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update_entry))
    monkeypatch.setattr(entity_registry_module, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        entity_registry_module,
        "async_entries_for_config_entry",
        lambda _registry, entry_id: registry.for_entry(entry_id),
    )
    monkeypatch.setattr(device_registry_module, "async_get", lambda _hass: _DeviceRegistry())
    return hass, registry


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
@pytest.mark.parametrize("old_version", range(1, 8))
async def test_every_distributed_config_entry_version_migrates_directly_to_v8(
    old_version: int, monkeypatch
) -> None:
    entry = SimpleNamespace(
        entry_id=f"entry-{old_version}",
        unique_id=f"http://pihole-{old_version}.example.test:80",
        version=old_version,
        data={"host": "pihole.example.test"},
    )
    hass, _registry = _migration_hass(entry, monkeypatch)

    assert await async_migrate_entry(hass, entry)
    source_id = entry.data[CONF_SOURCE_ID]
    assert str(UUID(source_id)) == source_id
    assert entry.data[CONF_SOURCE_GENERATION] == LEGACY_SOURCE_GENERATION
    assert entry.version == 8

    assert await async_migrate_entry(hass, entry)
    assert entry.data[CONF_SOURCE_ID] == source_id


@pytest.mark.asyncio
async def test_first_generation_store_import_is_idempotent_and_keeps_v1_untouched() -> None:
    source_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    legacy_data = {
        "cursor": 500,
        "clients": {
            "192.0.2.10": {
                "query_count": 42,
                "allowed_count": 31,
                "blocked_count": 11,
            }
        },
    }
    old = _Store(legacy_data)
    current = _Store()
    store = _IdentityStore(current, old, source_id, LEGACY_SOURCE_GENERATION)

    assert await store.async_load() == legacy_data
    expected = {
        "schema_version": STORAGE_VERSION,
        "source_id": source_id,
        "source_generation": LEGACY_SOURCE_GENERATION,
        "data": legacy_data,
    }
    assert current.value == expected
    assert old.value == legacy_data
    assert len(current.saves) == 1

    assert await store.async_load() == legacy_data
    assert len(current.saves) == 1
    assert old.value == legacy_data

    updated = {**legacy_data, "cursor": 501}
    await store.async_save(updated)
    assert current.value["data"] == updated
    assert current.value["source_id"] == source_id
    assert current.value["source_generation"] == LEGACY_SOURCE_GENERATION
    assert old.value == legacy_data


@pytest.mark.asyncio
async def test_replacement_generation_does_not_import_entry_scoped_v1_cursor() -> None:
    legacy_data = {
        "cursor": 900,
        "clients": {"192.0.2.10": {"query_count": 42}},
    }
    old = _Store(legacy_data)
    current = _Store()
    store = _IdentityStore(
        current,
        old,
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    )

    assert await store.async_load() is None
    assert current.value is None
    assert current.saves == []
    assert old.value == legacy_data
    assert old.saves == []


@pytest.mark.asyncio
async def test_replacement_aggregate_scrubs_legacy_backup_without_importing_it() -> None:
    old = _Store(
        {
            "cursor": 900,
            "clients": {
                "192.0.2.10": {
                    "query_count": 42,
                    "last_query": {"domain": "private.example.test"},
                    "recent_queries": [{"id": 901}],
                }
            },
        }
    )
    current = _Store()
    store = _IdentityStore(
        current,
        old,
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        aggregate_mode=True,
    )

    assert await store.async_load() is None
    assert current.value is None
    assert old.value == {
        "cursor": 900,
        "clients": {"192.0.2.10": {"query_count": 42}},
    }


@pytest.mark.asyncio
async def test_v2_store_with_different_identity_is_rejected() -> None:
    current = _Store(
        {
            "schema_version": STORAGE_VERSION,
            "source_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "source_generation": LEGACY_SOURCE_GENERATION,
            "data": {"cursor": 500},
        }
    )
    store = _IdentityStore(current, _Store({"cursor": 1}), "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", LEGACY_SOURCE_GENERATION)

    with pytest.raises(HomeAssistantError, match="no coincide"):
        await store.async_load()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, True, 7, "bad", []])
async def test_v2_store_with_non_mapping_payload_is_rejected(payload) -> None:
    current = _Store(
        {
            "schema_version": STORAGE_VERSION,
            "source_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "source_generation": LEGACY_SOURCE_GENERATION,
            "data": payload,
        }
    )
    store = _IdentityStore(
        current,
        _Store({"cursor": 1}),
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        LEGACY_SOURCE_GENERATION,
    )

    with pytest.raises(HomeAssistantError, match="esquema"):
        await store.async_load()


@pytest.mark.asyncio
@pytest.mark.parametrize("schema_version", [True, 2.0, "2"])
async def test_v2_store_requires_integer_schema_version(schema_version) -> None:
    current = _Store(
        {
            "schema_version": schema_version,
            "source_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "source_generation": LEGACY_SOURCE_GENERATION,
            "data": {},
        }
    )
    store = _IdentityStore(
        current,
        _Store(),
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        LEGACY_SOURCE_GENERATION,
    )

    with pytest.raises(HomeAssistantError, match="esquema"):
        await store.async_load()


@pytest.mark.asyncio
async def test_v2_malformed_client_fields_are_excluded_and_coverage_is_unknown() -> None:
    data = {
        "cursor": 500,
        "clients": {
            "192.0.2.10": {
                "query_count": 42,
                "allowed_count": True,
                "blocked_count": 1.7,
                "classifier_rates": "not-a-map",
            }
        },
    }
    current = _Store(
        {
            "schema_version": STORAGE_VERSION,
            "source_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "source_generation": LEGACY_SOURCE_GENERATION,
            "data": data,
        }
    )
    store = _IdentityStore(
        current,
        _Store(),
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        LEGACY_SOURCE_GENERATION,
    )

    prepared = await store.async_load()
    coordinator = _coordinator_for_store(store)
    await coordinator.async_load_cursor()

    summary = coordinator.client_data["192.0.2.10"]
    assert summary["query_count"] == 42
    assert "allowed_count" not in summary
    assert "blocked_count" not in summary
    assert "classifier_rates" not in summary or summary["classifier_rates"] == {
        "1": {"allowed": 0, "blocked": 0, "provisional": 0}
    }
    assert prepared["coverage"] == "unknown"
    assert prepared["coverage_reason"] == "invalid_store_state"
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "invalid_store_state"
    assert current.value["data"] == data


@pytest.mark.asyncio
async def test_legacy_store_import_tolerates_malformed_fields_and_keeps_valid_totals() -> None:
    legacy_data = {
        "cursor": 500,
        "clients": {
            "192.0.2.10": {
                "client_name": "Router",
                "client_ip": "192.0.2.10",
                "query_count": 42,
                "allowed_count": 31,
                "blocked_count": 11,
                "classifier_counts": ["malformed"],
                "recent_activity": [{"observed_at": [], "classification": []}],
                "recent_queries": {"not": "a list"},
                "last_query_id": [],
                "last_query": "malformed",
            }
        },
    }
    old = _Store(legacy_data)
    current = _Store()
    store = _IdentityStore(
        current,
        old,
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        LEGACY_SOURCE_GENERATION,
    )

    imported = await store.async_load()

    summary = imported["clients"]["192.0.2.10"]
    assert summary["query_count"] == 42
    assert summary["allowed_count"] == 31
    assert summary["blocked_count"] == 11
    assert "classifier_counts" not in summary
    assert "recent_activity" not in summary
    assert "recent_queries" not in summary
    assert "last_query_id" not in summary
    assert "last_query" not in summary
    assert imported["coverage"] == "unknown"
    assert imported["coverage_reason"] == "invalid_legacy_store"
    assert old.value == legacy_data
    assert current.value["data"] == imported
    coordinator = _coordinator_for_store(store)
    await coordinator.async_load_cursor()
    assert coordinator.cursor == 500
    assert coordinator.client_data["192.0.2.10"]["query_count"] == 42
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "invalid_legacy_store"


@pytest.mark.asyncio
async def test_legacy_store_without_clients_preserves_cursor_and_is_unknown() -> None:
    old = _Store({"cursor": 500})
    current = _Store()
    store = _IdentityStore(
        current,
        old,
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        LEGACY_SOURCE_GENERATION,
    )

    imported = await store.async_load()

    assert imported == {"cursor": 500}
    assert current.value["data"] == {"cursor": 500}
    assert old.value == {"cursor": 500}
    coordinator = _coordinator_for_store(store)
    await coordinator.async_load_cursor()
    assert coordinator.cursor == 500
    assert coordinator.client_data == {}
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "missing_client_summaries"


@pytest.mark.asyncio
@pytest.mark.parametrize("root_value", [True, 17, "malformed", []])
async def test_non_mapping_legacy_store_roots_fail_closed_as_invalid_store(root_value) -> None:
    old = _Store(root_value)
    current = _Store()
    store = _IdentityStore(
        current,
        old,
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        LEGACY_SOURCE_GENERATION,
    )

    imported = await store.async_load()
    coordinator = _coordinator_for_store(store)
    await coordinator.async_load_cursor()

    assert imported == root_value
    assert coordinator.coverage == "unknown"
    assert coordinator.coverage_reason == "invalid_legacy_store"
    assert old.value == root_value
    assert current.value is None


@pytest.mark.asyncio
async def test_null_legacy_store_is_indistinguishable_from_absent_store() -> None:
    # HA Store returns None for both an absent file and stored JSON null.
    old = _Store(None)
    current = _Store()
    store = _IdentityStore(
        current,
        old,
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        LEGACY_SOURCE_GENERATION,
    )
    assert await store.async_load() is None
    assert current.value is None


def test_legacy_entity_and_device_ids_stay_stable_for_first_generation() -> None:
    entry = SimpleNamespace(
        entry_id="entry-a",
        unique_id="https://pihole.example.test:443",
        data={CONF_SOURCE_ID: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", CONF_SOURCE_GENERATION: LEGACY_SOURCE_GENERATION},
    )

    assert _client_entity_unique_id(entry, "192.0.2.10", "queries") == (
        "https://pihole.example.test:443_192.0.2.10"
    )
    assert _client_entity_unique_id(entry, "192.0.2.10", "allowed") == (
        "https://pihole.example.test:443_192.0.2.10_allowed"
    )
    assert _client_device_identifier(entry, "192.0.2.10") == (
        DOMAIN,
        "entry-a_192.0.2.10",
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
        f"sensor.phe_{generation.replace('-', '_')}_192_0_2_10_permitidas"
    )


@pytest.mark.parametrize(
    ("classification", "localized"),
    [
        ("allowed", "permitidas"),
        ("blocked", "bloqueadas"),
        ("provisional", "provisionales"),
    ],
)
def test_versioned_class_entities_get_metric_and_epoch_in_canonical_id(
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
        unique_id=(
            f"{source_id}_{generation}_192.0.2.10_{classification}_v2"
        ),
    )

    assert _canonical_entity_id(entry, entity) == (
        f"sensor.phe_{generation.replace('-', '_')}_192_0_2_10_{localized}_v2"
    )


def test_two_sources_with_same_client_ip_have_distinct_entity_and_device_ids() -> None:
    entries = [
        SimpleNamespace(
            entry_id="entry-a",
            unique_id="http://one.example.test:80",
            data={CONF_SOURCE_ID: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", CONF_SOURCE_GENERATION: LEGACY_SOURCE_GENERATION},
        ),
        SimpleNamespace(
            entry_id="entry-b",
            unique_id="http://two.example.test:80",
            data={CONF_SOURCE_ID: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", CONF_SOURCE_GENERATION: LEGACY_SOURCE_GENERATION},
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
                CONF_SOURCE_GENERATION: LEGACY_SOURCE_GENERATION,
            },
        ),
        SimpleNamespace(
            entry_id="entry-b",
            unique_id="http://two.example.test:80",
            data={
                CONF_SOURCE_ID: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                CONF_SOURCE_GENERATION: LEGACY_SOURCE_GENERATION,
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
    assert len(
        {
            _identity_storage_key(
                entry.data[CONF_SOURCE_ID], entry.data[CONF_SOURCE_GENERATION]
            )
            for entry in entries
        }
    ) == 2


@pytest.mark.asyncio
async def test_same_ip_sources_keep_independent_store_payloads() -> None:
    identities = [
        ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", LEGACY_SOURCE_GENERATION),
        ("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", LEGACY_SOURCE_GENERATION),
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
            _Store(),
            source_id,
            generation,
        )
        for (source_id, generation), cursor, count in zip(identities, (100, 900), (5, 9))
    ]

    loaded = [await store.async_load() for store in stores]

    assert [data["cursor"] for data in loaded] == [100, 900]
    assert [data["clients"]["192.0.2.10"]["query_count"] for data in loaded] == [5, 9]
    assert len({_identity_storage_key(*identity) for identity in identities}) == 2


def test_store_version_constants_keep_v1_reader_for_migration() -> None:
    assert LEGACY_STORAGE_VERSION == 1
    assert STORAGE_VERSION == 2
    assert STORAGE_KEY_PREFIX == f"{DOMAIN}_cursor"


@pytest.mark.asyncio
@pytest.mark.parametrize("old_version", range(1, 8))
async def test_identity_upgrade_preserves_registered_custom_entity_id_from_every_version(
    old_version: int, monkeypatch
) -> None:
    entity = SimpleNamespace(
        entity_id="sensor.my_custom_dns_total",
        unique_id="https://pihole.example.test:443_192.0.2.10",
        config_entry_id=f"entry-custom-{old_version}",
        domain="sensor",
        device_id="device-custom",
    )
    entry = SimpleNamespace(
        entry_id=f"entry-custom-{old_version}",
        unique_id="https://pihole.example.test:443",
        version=old_version,
        data={"host": "pihole.example.test"},
    )
    hass, registry = _migration_hass(entry, monkeypatch, [entity])

    assert await async_migrate_entry(hass, entry)

    assert registry.async_get("sensor.my_custom_dns_total") is entity
    assert entity.unique_id == "https://pihole.example.test:443_192.0.2.10"
    assert entity.device_id == "device-custom"
