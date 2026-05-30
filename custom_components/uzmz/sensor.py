"""Sensor platform for UZMZ integration."""

import logging
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    ICON_ELECTRIC,
    ICON_WATER,
    ICON_CURRENCY,
    ICON_STATUS,
    ICON_UPDATE,
    UNIT_KWH,
    UNIT_CUBIC_METER,
    UNIT_CURRENCY,
)
from .coordinator import UZMZDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up UZMZ sensor entities."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    sensors = []
    
    # Electric meter sensors
    sensors.extend([
        UZMZSensor(
            coordinator,
            "electric",
            "remaining_unit",
            "Electric Remaining Units",
            UNIT_KWH,
            ICON_ELECTRIC,
            None,
            SensorStateClass.MEASUREMENT,
        ),
        UZMZSensor(
            coordinator,
            "electric",
            "remaining_balance",
            "Electric Remaining Balance",
            UNIT_CURRENCY,
            ICON_CURRENCY,
            None,
            SensorStateClass.MEASUREMENT,
        ),
        UZMZSensor(
            coordinator,
            "electric",
            "status",
            "Electric Meter Status",
            None,
            ICON_STATUS,
            None,
            None,
        ),
        UZMZSensor(
            coordinator,
            "electric",
            "last_updated",
            "Electric Last Updated",
            None,
            ICON_UPDATE,
            SensorDeviceClass.TIMESTAMP,
            None,
        ),
    ])
    
    # Water meter sensors
    sensors.extend([
        UZMZSensor(
            coordinator,
            "water",
            "remaining_unit",
            "Water Remaining Units",
            UNIT_CUBIC_METER,
            ICON_WATER,
            None,
            SensorStateClass.MEASUREMENT,
        ),
        UZMZSensor(
            coordinator,
            "water",
            "remaining_balance",
            "Water Remaining Balance",
            UNIT_CURRENCY,
            ICON_CURRENCY,
            None,
            SensorStateClass.MEASUREMENT,
        ),
        UZMZSensor(
            coordinator,
            "water",
            "status",
            "Water Meter Status",
            None,
            ICON_STATUS,
            None,
            None,
        ),
        UZMZSensor(
            coordinator,
            "water",
            "last_updated",
            "Water Last Updated",
            None,
            ICON_UPDATE,
            SensorDeviceClass.TIMESTAMP,
            None,
        ),
    ])

    # Integration-status sensors (track the integration's own polling, distinct
    # from the per-meter "last updated" sensors which reflect USMS billing time).
    sensors.extend([
        UZMZIntegrationStatusSensor(coordinator, "last_refresh"),
        UZMZIntegrationStatusSensor(coordinator, "next_refresh"),
    ])

    async_add_entities(sensors, True)


class UZMZSensor(CoordinatorEntity, SensorEntity):
    """Representation of a UZMZ sensor."""

    def __init__(
        self,
        coordinator: UZMZDataUpdateCoordinator,
        meter_type: str,
        sensor_type: str,
        name: str,
        unit: Optional[str],
        icon: str,
        device_class: Optional[SensorDeviceClass],
        state_class: Optional[SensorStateClass],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._meter_type = meter_type
        self._sensor_type = sensor_type
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{meter_type}_{sensor_type}"
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_has_entity_name = True

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return None
        
        meter_data = self.coordinator.data.get(self._meter_type, {})
        if not meter_data:
            return None
        
        value = meter_data.get(self._sensor_type)
        
        # Clean and convert numeric values
        if self._sensor_type in ["remaining_unit", "remaining_balance"]:
            if value:
                try:
                    cleaned = "".join(c for c in str(value) if c in "0123456789.")
                    return float(cleaned) if cleaned else 0.0
                except (ValueError, TypeError):
                    _LOGGER.warning("Could not convert value to float: %s", value)
                    return 0.0
            return 0.0

        # Parse timestamp strings into datetime objects
        if self._sensor_type == "last_updated" and value:
            try:
                # Try format with time: "11/03/2026 20:00:00"
                dt = datetime.strptime(str(value), "%d/%m/%Y %H:%M:%S")
            except ValueError:
                try:
                    # Try date only: "11/03/2026"
                    dt = datetime.strptime(str(value), "%d/%m/%Y")
                except ValueError:
                    _LOGGER.warning("Could not parse timestamp: %s", value)
                    return None
            return dt.replace(tzinfo=ZoneInfo("Asia/Brunei"))

        return value

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success and bool(
            self.coordinator.data.get(self._meter_type)
        )

    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device info for this sensor."""
        return {
            "identifiers": {(DOMAIN, f"{self._meter_type}_meter")},
            "name": f"UZMZ {self._meter_type.title()} Meter",
            "manufacturer": "USMS",
            "model": f"{self._meter_type.title()} Meter",
        }

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes."""
        if not self.coordinator.data:
            return {}

        meter_data = self.coordinator.data.get(self._meter_type, {})

        # Return all meter data as attributes
        return {
            "meter_type": self._meter_type,
            "status": meter_data.get("status"),
            "remaining_unit": meter_data.get("remaining_unit"),
            "remaining_balance": meter_data.get("remaining_balance"),
            "last_updated": meter_data.get("last_updated"),
        }


class UZMZIntegrationStatusSensor(CoordinatorEntity, SensorEntity):
    """Tracks the integration's polling activity (last/next refresh)."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: UZMZDataUpdateCoordinator,
        kind: str,
    ) -> None:
        """Initialize the integration-status sensor."""
        super().__init__(coordinator)
        self._kind = kind
        if kind == "last_refresh":
            self._attr_name = "Last Refresh"
            self._attr_icon = "mdi:cloud-sync"
        elif kind == "next_refresh":
            self._attr_name = "Next Refresh"
            self._attr_icon = "mdi:clock-outline"
        else:
            raise ValueError(f"Unknown integration-status kind: {kind}")
        self._attr_unique_id = f"{DOMAIN}_integration_{kind}"

    @property
    def native_value(self) -> Optional[datetime]:
        """Return the timestamp this sensor represents."""
        if self._kind == "last_refresh":
            return self.coordinator.last_successful_refresh

        # next_refresh: derive from last attempt + current update_interval.
        attempt = self.coordinator.last_attempt_time
        interval = self.coordinator.update_interval
        if attempt is None or interval is None:
            return None
        return attempt + interval

    @property
    def available(self) -> bool:
        """Stay available across transient failures.

        The whole point of these sensors is to surface refresh state during
        outages, so they shouldn't go "unavailable" the moment a poll fails.
        Once we've ever had a successful refresh, treat them as available.
        """
        return self.coordinator.last_successful_refresh is not None

    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device info for the integration-status device."""
        return {
            "identifiers": {(DOMAIN, "integration")},
            "name": "UZMZ Integration",
            "manufacturer": "USMS",
            "model": "Integration Status",
        }