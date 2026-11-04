"""The activity card keeps every user-visible string in its translation files."""

from __future__ import annotations

import json
import re
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "pihole_queries"
CARD = (COMPONENT / "frontend" / "phe-activity-card.js").read_text(encoding="utf-8")


def _catalog(language: str) -> dict[str, str]:
    catalog = json.loads((COMPONENT / "translations" / f"{language}.json").read_text(encoding="utf-8"))
    return catalog["selector"]["card"]["options"]


def test_every_shipped_language_has_the_same_card_keys() -> None:
    codes = [path.stem for path in (COMPONENT / "translations").glob("*.json")]
    assert "en" in codes
    reference = set(_catalog("en"))
    assert all(set(_catalog(code)) == reference for code in codes)
    assert all(value.strip() for code in codes for value in _catalog(code).values())


def test_translations_live_only_in_the_integration_catalogs() -> None:
    assert not (COMPONENT / "frontend" / "translations").exists()
    assert not (COMPONENT / "strings.json").exists()


def test_card_only_uses_keys_that_exist_and_leaves_none_unused() -> None:
    used = set(re.findall(r"\bt\.([a-z_]+)\b", CARD))
    # Keys addressed dynamically through the validation result.
    used |= {"unavailable", "too_many", "bigger_than_range"}
    keys = set(_catalog("en"))
    assert used - keys == set()
    assert keys - used == set()


def test_no_user_visible_literals_in_templates() -> None:
    text_nodes = re.findall(r"<[a-z0-9]+[^<>]*>([^<>${}\n]*[A-Za-zÁ-ú]{3,}[^<>${}\n]*)<", CARD)
    assert text_nodes == []
