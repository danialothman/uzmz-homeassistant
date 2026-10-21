"""Constants for the UZMZ integration."""

from datetime import timedelta
from zoneinfo import ZoneInfo

DOMAIN = "uzmz"
# USMS times are Brunei time. Built once at import: ZoneInfo reads the
# timezone database from disk the first time, which mustn't happen in the
# event loop.
BRUNEI_TZ = ZoneInfo("Asia/Brunei")
# Config entry title. Never include the IC number: titles appear in the UI,
# in log messages and in notifications.
ENTRY_TITLE = "UZMZ"
# Keep in step with manifest.json (a test checks).
VERSION = "1.9.1"
# Sent with every request, so USMS can see what the traffic is and who to
# contact, rather than a generic HTTP library's default.
USER_AGENT = f"UZMZ/{VERSION} (Home Assistant integration; +https://github.com/danialothman/uzmz-homeassistant)"

# Configuration keys
CONF_IC_NUMBER = "ic_number"
CONF_PASSWORD = "password"
CONF_BASE_URL = "base_url"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SESSION_COOKIES = "session_cookies"
# Option: keep the password for sign-in renewals (off: ask for it every time).
CONF_SAVE_PASSWORD = "save_password"
# Set when USMS rejected the saved password during an automatic sign-in, so
# it isn't tried again (5 failures lock the account). A manual sign-in clears it.
CONF_AUTO_SIGN_IN_BLOCKED = "auto_sign_in_blocked"
# When the current session was found ended, and whether the one automatic
# sign-in for it was used. Kept in the entry so a restart (or setup retries)
# during the wait neither restarts it nor tries twice. A new session clears them.
CONF_SESSION_ENDED = "session_ended"
CONF_AUTO_SIGN_IN_TRIED = "auto_sign_in_tried"

# Default values
DEFAULT_BASE_URL = "https://www.usms.com.bn/SmartMeter/"
DEFAULT_SCAN_INTERVAL = timedelta(hours=1)
# How often to touch the USMS session so it doesn't idle out between polls.
# ASP.NET's default session timeout is 20 minutes.
KEEPALIVE_INTERVAL = timedelta(minutes=5)
# With a saved password, how long to wait after USMS ends the session before
# signing in again automatically. USMS allows one session per account, so
# signing in straight away would cut off someone using the website or app
# (usually what ended the session, e.g. to top up).
AUTO_SIGN_IN_DELAY = timedelta(minutes=60)
WATER_SCAN_INTERVAL = timedelta(hours=24)

# Sensor types
SENSOR_TYPE_ELECTRIC = "electric"
SENSOR_TYPE_WATER = "water"

# Sensor attributes
ATTR_METER_STATUS = "meter_status"
ATTR_REMAINING_UNIT = "remaining_unit"
ATTR_REMAINING_BALANCE = "remaining_balance"
ATTR_LAST_UPDATED = "last_updated"
ATTR_METER_TYPE = "meter_type"

# Field mappings from HTML
# Matched on each card field's caption (e.g. "Remaining Unit:"), not its
# position: USMS has shifted the layout item indexes before.
FIELD_MAPPINGS = [
    "Meter Status",
    "Remaining Unit",
    "Remaining Balance",
    "Last Updated",
]

# Image sources for meter types
IMG_ELECTRIC = "Images/Bulb.png"
IMG_WATER = "Images/WaterPipe.png"

# Form field names
FORM_VIEWSTATE = "__VIEWSTATE"
FORM_EVENTVALIDATION = "__EVENTVALIDATION"
FORM_VIEWSTATEGENERATOR = "__VIEWSTATEGENERATOR"
FORM_USERNAME = "ASPxRoundPanel1$txtUsername"
FORM_PASSWORD = "ASPxRoundPanel1$txtPassword"
FORM_LOGIN_BUTTON = "ASPxRoundPanel1$btnLogin"
FORM_CAPTCHA = "ASPxRoundPanel1$ASPxCaptcha1$TB"

# HTML element IDs
ELEM_USERNAME_SPAN = "ASPxPanel1_lblUserName"
ELEM_LOGIN_ERROR = "pcErr_lblErrMsg"
ELEM_CAPTCHA_BOX = "ASPxRoundPanel1_ASPxCaptcha1"
ELEM_CAPTCHA_IMG = "ASPxRoundPanel1_ASPxCaptcha1_IMG"
ELEM_CAPTCHA_ERROR = "ASPxRoundPanel1_ASPxCaptcha1_TB_EC"
ELEM_IFRAME = "MyFrame"
# USMS platform version label on MainPage (only shown when logged in).
ELEM_PLATFORM_VERSION = "lblVer"

# The USMS platform version this release was tested against. A different
# version usually means USMS changed its website (6.1.1001 brought the
# captcha login), so the coordinator warns once when it changes.
KNOWN_PLATFORM_VERSION = "6.1.1001"

# Units of measurement
UNIT_KWH = "kWh"
UNIT_CUBIC_METER = "m³"
UNIT_CURRENCY = "BND"

# Icons for entities
ICON_ELECTRIC = "mdi:lightning-bolt"
ICON_WATER = "mdi:water"
ICON_CURRENCY = "mdi:cash"
ICON_STATUS = "mdi:information-outline"
ICON_UPDATE = "mdi:update"
ICON_USAGE_UPDATED = "mdi:clock-check-outline"
# Usage-report bookkeeping stored in the config entry (see usage.py).
CONF_USAGE_ANCHORS = "usage_anchors"
CONF_USAGE_RATES = "usage_rates"
# Set by the config flow after a login so the next update re-anchors.
CONF_FRESH_SESSION = "fresh_session"
# Last USMS platform version seen, to warn once when it changes.
CONF_PLATFORM_VERSION = "platform_version"
# Recent report totals per meter, and when they last changed (USMS posted data).
CONF_USAGE_SEEN = "usage_seen"
CONF_USAGE_CHANGED = "usage_changed"
# Units bought per meter this calendar month, which sets the price of the next
# top-up (see usage.month_top_ups).
CONF_TOP_UPS = "top_ups"
# USMS top-up transaction numbers per meter that the price layers already include.
CONF_TOP_UP_SEEN = "top_up_seen"
UNIT_CURRENCY_PER_KWH = "BND/kWh"
UNIT_CURRENCY_PER_CUBIC_METER = "BND/m³"
ICON_TOP_UP = "mdi:cash-plus"
ICON_PRICE = "mdi:tag-outline"
ICON_ADVICE = "mdi:lightbulb-on-outline"
ICON_PRICE_CHANGE = "mdi:calendar-arrow-right"
