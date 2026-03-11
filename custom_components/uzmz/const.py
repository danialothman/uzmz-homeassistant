"""Constants for the UZMZ integration."""

from datetime import timedelta

DOMAIN = "uzmz"

# Configuration keys
CONF_IC_NUMBER = "ic_number"
CONF_PASSWORD = "password"
CONF_BASE_URL = "base_url"
CONF_PLATFORM_VERSION = "platform_version"
CONF_SCAN_INTERVAL = "scan_interval"

# Default values
DEFAULT_BASE_URL = "https://www.usms.com.bn/SmartMeter/"
DEFAULT_SCAN_INTERVAL = timedelta(hours=1)
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
FIELD_MAPPINGS = [
    ("Meter Status", lambda x: x.endswith("_5_Cap")),
    ("Remaining Unit", lambda x: x.endswith("_11_Cap")),
    ("Remaining Balance", lambda x: x.endswith("_12_Cap")),
    ("Last Updated", lambda x: x.endswith("_17_Cap")),
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
FORM_LOGOUT_BUTTON = "ASPxPanel1$btnLogout"

# HTML element IDs
ELEM_USERNAME_SPAN = "ASPxPanel1_lblUserName"
ELEM_VERSION_SPAN = "lblVer"
ELEM_IFRAME = "MyFrame"

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