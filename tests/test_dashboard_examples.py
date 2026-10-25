"""Contract checks for the distributable dashboard and automation examples."""

from pathlib import Path
import re

import yaml

from custom_components.pihole_queries.activity import BUCKET_SECONDS, SPAN_SECONDS

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_PATH = ROOT / "examples/dashboards/pihole-extended.yaml"
AUTOMATION_PATH = ROOT / "examples/automations/unusual-activity-alert.yaml"
GUIDE_PATH = ROOT / "docs/dashboard-examples.md"
READMES = [ROOT / "README.md", ROOT / "README.es.md"]
CARD_KEYS = {"type", "title", "entities", "bucket", "span", "chart_type", "log_scale", "show_overview", "names"}
ENTITY_ID = re.compile(
    r"(sensor|binary_sensor)\.(phe_192_0_2_\d+_(consultas_dns|consultas_en_el_ultimo_minuto|actividad_inusual)"
    r"|pi_hole_192_0_2_2_diagnostico_[a-z_]+)"
)


def _dashboard():
    return yaml.safe_load(DASHBOARD_PATH.read_text(encoding="utf-8"))


def _entity_ids(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("entity", "entity_id") and isinstance(item, str):
                found.add(item)
            found |= _entity_ids(item)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str) and "." in item:
                found.add(item)
            else:
                found |= _entity_ids(item)
    return found


def _validate_card(card: dict) -> None:
    assert set(card) <= CARD_KEYS
    if "bucket" in card:
        assert card["bucket"] in BUCKET_SECONDS
    if "span" in card:
        assert card["span"] in SPAN_SECONDS
    if "chart_type" in card:
        assert card["chart_type"] in {"line", "bar"}
    assert len(card.get("entities", [])) <= 12


def test_dashboard_views_and_card_options_are_valid() -> None:
    views = _dashboard()["views"]
    assert [view["path"] for view in views] == ["activity", "diagnostics"]
    cards = [card for view in views for card in view["cards"]]
    activity = [card for card in cards if card["type"] == "custom:phe-activity-card"]
    assert len(activity) == 1
    _validate_card(activity[0])
    assert {card["type"] for card in cards} <= {"custom:phe-activity-card", "entities", "history-graph"}


def test_examples_only_use_documentation_addresses_and_known_entities() -> None:
    text = (DASHBOARD_PATH.read_text(encoding="utf-8") + AUTOMATION_PATH.read_text(encoding="utf-8")).lower()
    assert not re.search(r"\b(?:10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)", text)
    ids = _entity_ids(_dashboard()) | _entity_ids(yaml.safe_load(AUTOMATION_PATH.read_text(encoding="utf-8")))
    assert ids
    assert all(ENTITY_ID.fullmatch(entity_id) for entity_id in ids), ids


def test_automation_only_reads_attributes_the_sensor_exposes() -> None:
    sensor_source = (ROOT / "custom_components/pihole_queries/binary_sensor.py").read_text(encoding="utf-8")
    automation = AUTOMATION_PATH.read_text(encoding="utf-8")
    used = set(re.findall(r"attributes\.([a-z_]+)", automation))
    assert used
    assert all(f'"{attribute}"' in sensor_source for attribute in used)
    parsed = yaml.safe_load(automation)
    assert parsed["triggers"][0]["to"] == "on"
    assert parsed["triggers"][0]["for"]


def test_readme_card_examples_use_supported_options() -> None:
    for readme in READMES:
        blocks = re.findall(r"```yaml\n(.*?)```", readme.read_text(encoding="utf-8"), re.S)
        cards = [yaml.safe_load(block) for block in blocks]
        cards = [card for card in cards if isinstance(card, dict) and card.get("type") == "custom:phe-activity-card"]
        assert cards, readme
        for card in cards:
            _validate_card(card)


def test_guide_and_readmes_reference_existing_files() -> None:
    for document in [GUIDE_PATH, *READMES]:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\]\((?!https?:|#|mailto:)([^)#\s]+)", text):
            assert (document.parent / target).exists(), (document.name, target)
