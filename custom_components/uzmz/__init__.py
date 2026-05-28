"""The UZMZ Utility Meter Monitor integration."""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    DOMAIN,
    CONF_IC_NUMBER,
    CONF_PASSWORD,
    CONF_BASE_URL,
    CONF_PLATFORM_VERSION,
    CONF_SCAN_INTERVAL,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
)
from .coordinator import UZMZDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up UZMZ from a config entry."""
    
    # Get configuration from entry
    ic_number = entry.data[CONF_IC_NUMBER]
    password = entry.data[CONF_PASSWORD]
    base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
    platform_version = entry.data.get(CONF_PLATFORM_VERSION)
    
    # Get scan interval from options or use default
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        DEFAULT_SCAN_INTERVAL.total_seconds()
    )
    update_interval = timedelta(seconds=scan_interval)
    
    # Create the data update coordinator
    coordinator = UZMZDataUpdateCoordinator(
        hass,
        ic_number=ic_number,
        password=password,
        base_url=base_url,
        platform_version=platform_version,
        update_interval=update_interval,
    )
    
    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()
    
    # Store coordinator
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Register update listener
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options for the config entry."""
    # Get the coordinator
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    # Update scan interval if changed
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        DEFAULT_SCAN_INTERVAL.total_seconds()
    )
    new_interval = timedelta(seconds=scan_interval)
    
    if coordinator._normal_update_interval != new_interval:
        coordinator._normal_update_interval = new_interval
        # If the coordinator is currently in fast-retry mode after a failure,
        # leave update_interval alone — it will be restored on next success.
        # Otherwise apply the new normal interval immediately.
        if coordinator.update_interval != new_interval and coordinator._consecutive_failures == 0:
            coordinator.update_interval = new_interval
        _LOGGER.info("Updated scan interval to %s", new_interval)