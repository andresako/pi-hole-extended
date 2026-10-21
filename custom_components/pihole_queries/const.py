"""Constants for the Pi-hole DNS query integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "pihole_queries"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_PASSWORD = "password"
CONF_USE_HTTPS = "use_https"
CONF_VERIFY_SSL = "verify_ssl"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 15
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 3600
REQUEST_TIMEOUT = 15
QUERY_BATCH_SIZE = 1000

PLATFORMS: list[Platform] = [Platform.SENSOR]
UPDATE_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}_cursor"
CLIENT_QUERY_HISTORY = 10

