"""Constants for the Flashforge integration."""

DOMAIN = "flashforge"
DEFAULT_NAME = "FlashForge"

CONF_SERIAL_NUMBER = "serial_number"

SCAN_INTERVAL = 30
# Upper limit for one update attempt, so a hanging printer can't stall updates.
UPDATE_TIMEOUT = 20
MAX_FAILED_UPDATES = 3
