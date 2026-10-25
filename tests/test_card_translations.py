"""The activity card keeps every user-visible string in its translation files."""

from __future__ import annotations

import json
import re
from pathlib import Path

FRONTEND = Path(__file__).parents[1] / "custom_components" / "pihole_queries" / "frontend"
CARD = (FRONTEND / "phe-activity-card.js").read_text(encoding="utf-8")


def _catalog(language: str) -> dict[str, str]:
    return json.loads((FRONTEND / "translations" / f"{language}.json").read_text(encoding="utf-8"))


def test_every_shipped_language_has_the_same_keys() -> None:
    languages = re.search(r"PHE_LANGUAGES = \[([^\]]*)\]", CARD).group(1)
    codes = re.findall(r'"([a-z]{2})"', languages)
    assert "en" in codes
    assert {path.stem for path in (FRONTEND / "translations").glob("*.json")} == set(codes)
    reference = set(_catalog("en"))
    assert all(set(_catalog(code)) == reference for code in codes)
    assert all(value.strip() for code in codes for value in _catalog(code).values())


def test_card_only_uses_keys_that_exist_and_leaves_none_unused() -> None:
    used = set(re.findall(r"\bt\.([A-Za-z]+)\b", CARD))
    # Keys addressed dynamically through the validation result.
    used |= {"unavailable", "tooMany", "biggerThanRange"}
    keys = set(_catalog("en"))
    assert used - keys == set()
    assert keys - used == set()


def test_no_user_visible_literals_in_templates() -> None:
    text_nodes = re.findall(r"<[a-z0-9]+[^<>]*>([^<>${}\n]*[A-Za-zÁ-ú]{3,}[^<>${}\n]*)<", CARD)
    assert text_nodes == []
