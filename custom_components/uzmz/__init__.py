"""The UZMZ Utility Meter Monitor integration."""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import USMSClient
from .const import (
    DOMAIN,
    CONF_BASE_URL,
    CONF_IC_NUMBER,
    CONF_PASSWORD,
    CONF_SAVE_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_SESSION_COOKIES,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    ENTRY_TITLE,
)
from .coordinator import UZMZDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up UZMZ from a config entry."""
    
    # Entries from before the save-password option kept the password; drop it
    # unless the user has opted in to keeping it.
    if CONF_PASSWORD in entry.data and not entry.options.get(CONF_SAVE_PASSWORD, False):
        data = dict(entry.data)
        data.pop(CONF_PASSWORD)
        hass.config_entries.async_update_entry(entry, data=data)

    # Entries used to be titled "UZMZ <IC number>", which put the IC number in
    # the UI, logs and notifications. Rename them unless the user renamed them.
    if entry.title == f"{ENTRY_TITLE} {entry.data.get(CONF_IC_NUMBER)}":
        hass.config_entries.async_update_entry(entry, title=ENTRY_TITLE)

    # Get configuration from entry
    base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
    
    # Get scan interval from options or use default
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        DEFAULT_SCAN_INTERVAL.total_seconds()
    )
    update_interval = timedelta(seconds=scan_interval)
    
    # Reuse the session logged in via the config/reauth flow. Entries without
    # one (or with an expired one) fail the first refresh with an auth error,
    # which prompts the user to re-authenticate with a captcha.
    client = USMSClient(
        base_url,
        cookies=entry.data.get(CONF_SESSION_COOKIES),
    )
    entry.async_on_unload(client.close)

    # Create the data update coordinator
    coordinator = UZMZDataUpdateCoordinator(
        hass,
        entry,
        client,
        update_interval=update_interval,
    )
    
    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()
    entry.async_on_unload(coordinator.async_start_keepalive())
    
    # Store coordinator
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok

