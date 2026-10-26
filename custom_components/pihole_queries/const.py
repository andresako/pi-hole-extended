"""Constants for the Pi-hole DNS query integration."""

from datetime import timedelta
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "pihole_queries"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_PASSWORD = "password"
CONF_USE_HTTPS = "use_https"
CONF_VERIFY_SSL = "verify_ssl"
CONF_RECONFIGURE_SOURCE_ACTION = "source_action"
CONF_SOURCE_ID = "source_id"
CONF_SOURCE_GENERATION = "source_generation"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_RETENTION_MODE = "retention_mode"
CONF_ACTIVITY_MODE = "activity_mode"
CONF_UNUSUAL_ACTIVITY = "unusual_activity"
RETENTION_DETAIL = "detail"
RETENTION_AGGREGATE = "aggregate"
ACTIVITY_ALL = "all"
ACTIVITY_BLOCKED = "blocked"
ACTIVITY_NONE = "none"
UNUSUAL_OFF = "off"
UNUSUAL_LOW = "low"
UNUSUAL_MEDIUM = "medium"
UNUSUAL_HIGH = "high"

DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 15
DEFAULT_RETENTION_MODE = RETENTION_DETAIL
DEFAULT_ACTIVITY_MODE = ACTIVITY_ALL
DEFAULT_UNUSUAL_ACTIVITY = UNUSUAL_MEDIUM
MIN_SCAN_INTERVAL = 1
MAX_SCAN_INTERVAL = 3600
REQUEST_TIMEOUT = 15
QUERY_BATCH_SIZE = 1000

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]
UPDATE_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)

LEGACY_STORAGE_VERSION = 1
STORAGE_VERSION = 2
STORAGE_KEY_PREFIX = f"{DOMAIN}_cursor"
LEGACY_SOURCE_GENERATION = "legacy"
RECONFIGURE_SAME_SOURCE = "same_source"
RECONFIGURE_NEW_SOURCE = "new_source"


CARD_URL_BASE = f"/{DOMAIN}_static"
CARD_FILENAME = "phe-activity-card.js"


class Coverage(StrEnum):
    """Whether ingestion can prove it has seen every query since the cursor."""

    UNKNOWN = "unknown"
    COMPLETE = "complete"
    PARTIAL = "partial"


class CoverageReason(StrEnum):
    """Why coverage is not complete; values are persisted in the Store."""

    BASELINE_UNVERIFIED = "baseline_unverified"
    BATCH_INTERRUPTED = "batch_interrupted"
    CURSOR_NOT_REACHED = "cursor_not_reached"
    EMPTY_HISTORY = "empty_history"
    INVALID_CLIENT_SUMMARIES = "invalid_client_summaries"
    INVALID_LEGACY_CURSOR = "invalid_legacy_cursor"
    INVALID_LEGACY_STORE = "invalid_legacy_store"
    INVALID_QUERY_IDS = "invalid_query_ids"
    INVALID_STORE_STATE = "invalid_store_state"
    LEGACY_CURSOR = "legacy_cursor"
    MISSING_CLIENT_SUMMARIES = "missing_client_summaries"
    NO_BASELINE = "no_baseline"
    PAGINATION_STALLED = "pagination_stalled"
    QUERY_ID_RESET = "query_id_reset"
    RETENTION_TRUNCATION = "retention_truncation"
