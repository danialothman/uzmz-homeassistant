"""Constants for the UZMZ integration."""

from datetime import timedelta

DOMAIN = "uzmz"

# Configuration keys
CONF_IC_NUMBER = "ic_number"
CONF_PASSWORD = "password"
CONF_BASE_URL = "base_url"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SESSION_COOKIES = "session_cookies"

# Default values
DEFAULT_BASE_URL = "https://www.usms.com.bn/SmartMeter/"
DEFAULT_SCAN_INTERVAL = timedelta(hours=1)
# How often to touch the USMS session so it doesn't idle out between polls.
# ASP.NET's default session timeout is 20 minutes.
KEEPALIVE_INTERVAL = timedelta(minutes=5)
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