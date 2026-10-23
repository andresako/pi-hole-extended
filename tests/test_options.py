"""Retention options accept only the documented modes."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.pihole_queries.config_flow import _schema
from custom_components.pihole_queries.const import (
    ACTIVITY_ALL,
    ACTIVITY_BLOCKED,
    ACTIVITY_NONE,
    CONF_ACTIVITY_MODE,
    CONF_RETENTION_MODE,
    CONF_SCAN_INTERVAL,
    RETENTION_AGGREGATE,
    RETENTION_DETAIL,
)


def test_options_schema_defaults_to_legacy_detail_and_all_activity() -> None:
    schema = _schema(include_connection=False, include_retention=True)
    result = schema({CONF_SCAN_INTERVAL: 15})

    assert result[CONF_RETENTION_MODE] == RETENTION_DETAIL
    assert result[CONF_ACTIVITY_MODE] == ACTIVITY_ALL


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (CONF_RETENTION_MODE, RETENTION_AGGREGATE),
        (CONF_ACTIVITY_MODE, ACTIVITY_BLOCKED),
        (CONF_ACTIVITY_MODE, ACTIVITY_NONE),
    ],
)
def test_options_schema_accepts_documented_modes(field: str, value: str) -> None:
    schema = _schema(include_connection=False, include_retention=True)
    result = schema(
        {
            CONF_SCAN_INTERVAL: 30,
            CONF_RETENTION_MODE: RETENTION_DETAIL,
            CONF_ACTIVITY_MODE: ACTIVITY_ALL,
            field: value,
        }
    )

    assert result[field] == value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (CONF_RETENTION_MODE, "privacy-ish"),
        (CONF_ACTIVITY_MODE, "some"),
    ],
)
def test_options_schema_rejects_unknown_modes(field: str, value: str) -> None:
    schema = _schema(include_connection=False, include_retention=True)
    with pytest.raises(vol.Invalid):
        schema(
            {
                CONF_SCAN_INTERVAL: 30,
                CONF_RETENTION_MODE: RETENTION_DETAIL,
                CONF_ACTIVITY_MODE: ACTIVITY_ALL,
                field: value,
            }
        )
