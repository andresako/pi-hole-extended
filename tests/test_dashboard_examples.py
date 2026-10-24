"""Contract checks for the distributable native dashboard example."""

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_PATH = ROOT / "examples/dashboards/pihole-extended.yaml"
GUIDE_PATH = ROOT / "docs/dashboard-examples.md"


def test_dashboard_has_four_supported_native_views_and_navigation() -> None:
    dashboard = yaml.safe_load(DASHBOARD_PATH.read_text(encoding="utf-8"))

    assert [view["title"] for view in dashboard["views"]] == [
        "Summary",
        "Clients",
        "Details",
        "Diagnostics",
    ]
    assert [view["path"] for view in dashboard["views"]] == [
        "summary",
        "clients",
        "details",
        "diagnostics",
    ]
    assert {
        card["type"]
        for view in dashboard["views"]
        for card in view["cards"]
    } == {"markdown", "entities", "history-graph"}


def test_all_entity_ids_are_manual_placeholders_documented_in_guide() -> None:
    dashboard = yaml.safe_load(DASHBOARD_PATH.read_text(encoding="utf-8"))
    dashboard_text = DASHBOARD_PATH.read_text(encoding="utf-8")
    guide = GUIDE_PATH.read_text(encoding="utf-8")
    entity_ids = {
        row["entity"]
        for view in dashboard["views"]
        for card in view["cards"]
        for row in card.get("entities", [])
        if isinstance(row, dict) and "entity" in row
    }
    entity_ids.update(re.findall(r"sensor\.replace_with_[a-z0-9_]+", dashboard_text))

    assert entity_ids
    assert all(re.fullmatch(r"sensor\.replace_with_[a-z0-9_]+", eid) for eid in entity_ids)
    documented = set(re.findall(r"^\| `([a-z0-9_]+)` \| ([^|]+) \|$", guide, re.MULTILINE))
    expected = {
        **{f"client_{n}_queries": "Consultas DNS" for n in range(1, 5)},
        "client_1_allowed_v1": "Permitidas (v1)",
        "client_2_allowed_v1": "Permitidas (v1)",
        **{f"client_{n}_blocked_v1": "Bloqueadas (v1)" for n in range(1, 4)},
        "client_1_provisional_v1": "Provisionales (v1)",
        "client_2_provisional_v1": "Provisionales (v1)",
        "unknown_client_queries": "Consultas DNS",
        "entry_communication_status": "Estado de comunicación",
        "entry_last_successful_read": "Última lectura correcta",
        "entry_last_query_observed": "Última consulta observada",
        "entry_ingestion_coverage": "Cobertura de ingesta",
    }
    suffixes = {eid.removeprefix("sensor.replace_with_") for eid in entity_ids}
    assert suffixes == set(expected)
    for suffix, semantic in expected.items():
        if suffix.startswith("entry_"):
            labels = (f"Per-entry diagnostic **{semantic}**", f"Diagnóstico por entrada **{semantic}**")
        elif suffix == "unknown_client_queries":
            labels = ("**Consultas DNS** for the unknown client, when present", "**Consultas DNS** del cliente desconocido, si existe")
        else:
            labels = (f"Client's **{semantic}** sensor", f"Sensor **{semantic}** del cliente")
        for label in labels:
            assert (suffix, label) in documented


def test_summary_exposes_recent_observed_activity_without_claiming_source_time() -> None:
    dashboard = yaml.safe_load(DASHBOARD_PATH.read_text(encoding="utf-8"))
    summary = dashboard["views"][0]
    recent_card = next(
        card for card in summary["cards"]
        if card["type"] == "markdown" and "queries_observed_last_minute" in card["content"]
    )

    assert "Activity observed by HA · last minute" in recent_card["content"]
    assert "historical hourly volume" in recent_card["content"]
    assert "polling interval" in recent_card["content"]
    assert "until the next poll" in recent_card["content"]
    for client in range(1, 4):
        assert f"sensor.replace_with_client_{client}_queries" in recent_card["content"]
    assert "blocked_v1" not in recent_card["content"]


def test_unknown_client_label_is_compact_for_narrow_screens() -> None:
    dashboard = yaml.safe_load(DASHBOARD_PATH.read_text(encoding="utf-8"))
    clients = dashboard["views"][1]
    unknown = next(
        row
        for card in clients["cards"]
        for row in card.get("entities", [])
        if row.get("entity") == "sensor.replace_with_unknown_client_queries"
    )

    assert unknown["name"] == "Unknown · DNS"


def test_guide_distinguishes_zero_percentage_from_unknown() -> None:
    guide = GUIDE_PATH.read_text(encoding="utf-8")

    assert "The YAML was imported with manual entity mapping" in guide
    assert "El YAML se importó con asignación manual de entidades" in guide
    assert "A real 0% remains 0" in guide
    assert "the attribute is unknown rather than 0%" in guide
    assert "Un 0 % real sigue siendo 0" in guide
    assert "el atributo queda desconocido" in guide


def test_distribution_example_contains_no_prototype_fixture_or_private_data() -> None:
    text = DASHBOARD_PATH.read_text(encoding="utf-8").lower()

    assert "prototype_fixture_" not in text
    assert "example.test" not in text
    assert not re.search(r"\b(?:10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)", text)
    assert "last 24 hours" in text
    assert "original event time" in text
