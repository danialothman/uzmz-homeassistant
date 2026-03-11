"""Sensor platform for UZMZ integration."""

import logging
from typing import Any, Dict, Optional

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
            SensorDeviceClass.ENERGY,
            SensorStateClass.MEASUREMENT,
        ),
        UZMZSensor(
            coordinator,
            "electric",
            "remaining_balance",
            "Electric Remaining Balance",
            UNIT_CURRENCY,
            ICON_CURRENCY,
            SensorDeviceClass.MONETARY,
            None,
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
            SensorDeviceClass.WATER,
            SensorStateClass.MEASUREMENT,
        ),
        UZMZSensor(
            coordinator,
            "water",
            "remaining_balance",
            "Water Remaining Balance",
            UNIT_CURRENCY,
            ICON_CURRENCY,
            SensorDeviceClass.MONETARY,
            None,
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
                # Remove any non-numeric characters except decimal point
                try:
                    # Remove currency symbols, commas, and other non-numeric chars
                    cleaned = "".join(c for c in str(value) if c.isdigit() or c == ".")
                    return float(cleaned) if cleaned else 0.0
                except (ValueError, TypeError):
                    _LOGGER.warning("Could not convert value to float: %s", value)
                    return 0.0
            return 0.0
        
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