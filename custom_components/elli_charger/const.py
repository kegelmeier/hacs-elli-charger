"""Constants for the Elli Charger integration."""

DOMAIN = "elli_charger"

DEFAULT_NAME = "Elli Charger"

CONF_REFRESH_TOKEN = "refresh_token"
CONF_CALLBACK_URL = "callback_url"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL_MINUTES = 5
MIN_SCAN_INTERVAL_MINUTES = 2
MAX_SCAN_INTERVAL_MINUTES = 60

# Refresh the access token this long before it expires.
TOKEN_EXPIRY_MARGIN_SECONDS = 120

# Completed charging records are re-read in full every this many polls; the
# polls in between only ask for records newer than the last one we know.
RECORDS_FULL_RESYNC_EVERY_N_POLLS = 288

RECORDS_PAGE_SIZE = 100

STATUS_IDLE = "idle"
STATUS_CONNECTED = "connected"
STATUS_CHARGING = "charging"
STATUS_OPTIONS = [STATUS_IDLE, STATUS_CONNECTED, STATUS_CHARGING]
