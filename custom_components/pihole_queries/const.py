"""Constants for the Pi-hole DNS query integration."""

from datetime import timedelta

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
RETENTION_DETAIL = "detail"
RETENTION_AGGREGATE = "aggregate"
ACTIVITY_ALL = "all"
ACTIVITY_BLOCKED = "blocked"
ACTIVITY_NONE = "none"

DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 15
DEFAULT_RETENTION_MODE = RETENTION_DETAIL
DEFAULT_ACTIVITY_MODE = ACTIVITY_ALL
MIN_SCAN_INTERVAL = 1
MAX_SCAN_INTERVAL = 3600
REQUEST_TIMEOUT = 15
QUERY_BATCH_SIZE = 1000

PLATFORMS: list[Platform] = [Platform.SENSOR]
UPDATE_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)

LEGACY_STORAGE_VERSION = 1
STORAGE_VERSION = 2
STORAGE_KEY_PREFIX = f"{DOMAIN}_cursor"
LEGACY_SOURCE_GENERATION = "legacy"
RECONFIGURE_SAME_SOURCE = "same_source"
RECONFIGURE_NEW_SOURCE = "new_source"


CARD_URL_BASE = f"/{DOMAIN}_static"
CARD_FILENAME = "phe-activity-card.js"
