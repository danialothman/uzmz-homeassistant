"""Sensor platform for UZMZ integration."""

import logging
from datetime import date, datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    ICON_ELECTRIC,
    ICON_WATER,
    ICON_ADVICE,
    ICON_CURRENCY,
    ICON_PRICE,
    ICON_PRICE_CHANGE,
    ICON_STATUS,
    ICON_TOP_UP,
    ICON_UPDATE,
    ICON_USAGE_UPDATED,
    KNOWN_PLATFORM_VERSION,
    UNIT_KWH,
    UNIT_CUBIC_METER,
    UNIT_CURRENCY,
    UNIT_CURRENCY_PER_CUBIC_METER,
    UNIT_CURRENCY_PER_KWH,
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
        UZMZSensor(
            coordinator,
            "electric",
            "usage_updated",
            "Electric Usage Updated",
            None,
            ICON_USAGE_UPDATED,
            SensorDeviceClass.TIMESTAMP,
            None,
        ),
        # What a top-up buys now (prices restart each month; see usage.py).
        UZMZSensor(
            coordinator,
            "electric",
            "topped_up_this_month",
            "Electric Topped Up This Month",
            UNIT_CURRENCY,
            ICON_TOP_UP,
            SensorDeviceClass.MONETARY,
            None,
        ),
        UZMZSensor(
            coordinator,
            "electric",
            "top_up_price",
            "Electric Top-up Price",
            UNIT_CURRENCY_PER_KWH,
            ICON_PRICE,
            None,
            None,
        ),
        UZMZSensor(
            coordinator,
            "electric",
            "top_up_left_at_price",
            "Electric Top-up Left at This Price",
            UNIT_CURRENCY,
            ICON_TOP_UP,
            SensorDeviceClass.MONETARY,
            None,
        ),
        UZMZSensor(
            coordinator,
            "electric",
            "top_up_advice",
            "Electric Top-up Advice",
            None,
            ICON_ADVICE,
            None,
            None,
        ),
        UZMZSensor(
            coordinator,
            "electric",
            "price_change",
            "Electric Next Price Change",
            None,
            ICON_PRICE_CHANGE,
            SensorDeviceClass.DATE,
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
        UZMZSensor(
            coordinator,
            "water",
            "usage_updated",
            "Water Usage Updated",
            None,
            ICON_USAGE_UPDATED,
            SensorDeviceClass.TIMESTAMP,
            None,
        ),
        # What a top-up buys now (prices restart each month; see usage.py).
        UZMZSensor(
            coordinator,
            "water",
            "topped_up_this_month",
            "Water Topped Up This Month",
            UNIT_CURRENCY,
            ICON_TOP_UP,
            SensorDeviceClass.MONETARY,
            None,
        ),
        UZMZSensor(
            coordinator,
            "water",
            "top_up_price",
            "Water Top-up Price",
            UNIT_CURRENCY_PER_CUBIC_METER,
            ICON_PRICE,
            None,
            None,
        ),
        UZMZSensor(
            coordinator,
            "water",
            "top_up_left_at_price",
            "Water Top-up Left at This Price",
            UNIT_CURRENCY,
            ICON_TOP_UP,
            SensorDeviceClass.MONETARY,
            None,
        ),
        UZMZSensor(
            coordinator,
            "water",
            "top_up_advice",
            "Water Top-up Advice",
            None,
            ICON_ADVICE,
            None,
            None,
        ),
        UZMZSensor(
            coordinator,
            "water",
            "price_change",
            "Water Next Price Change",
            None,
            ICON_PRICE_CHANGE,
            SensorDeviceClass.DATE,
            None,
        ),
    ])

    # Integration-status sensors (track the integration's own polling, distinct
    # from the per-meter "last updated" sensors which reflect USMS billing time).
    sensors.extend([
        UZMZIntegrationStatusSensor(coordinator, "last_refresh"),
        UZMZIntegrationStatusSensor(coordinator, "next_refresh"),
        UZMZPlatformVersionSensor(coordinator),
    ])

    # No update_before_add: the coordinator already fetched in setup, and
    # updating each entity would make every one request another refresh.
    async_add_entities(sensors)


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
        
        # Clean and convert numeric values (already numbers when they were
        # brought up to date from the usage reports)
        if self._sensor_type in ["remaining_unit", "remaining_balance"]:
            if isinstance(value, (int, float)):
                return value
            if value:
                try:
                    cleaned = "".join(c for c in str(value) if c in "0123456789.")
                    return float(cleaned) if cleaned else 0.0
                except (ValueError, TypeError):
                    _LOGGER.warning("Could not convert value to float: %s", value)
                    return 0.0
            return 0.0

        # When the units in use now run out (ISO date)
        if self._sensor_type == "price_change":
            return date.fromisoformat(value) if value else None

        # When UZMZ last saw USMS post new usage (ISO timestamp)
        if self._sensor_type == "usage_updated":
            return datetime.fromisoformat(value) if value else None

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

        attributes = {
            "meter_type": self._meter_type,
            "status": meter_data.get("status"),
            "remaining_unit": meter_data.get("remaining_unit"),
            "remaining_balance": meter_data.get("remaining_balance"),
            "last_updated": meter_data.get("last_updated"),
        }
        # How the current readings were derived from the last sign-in (see
        # usage.py). The balance is an estimate: sign-in balance minus usage
        # since then at the learned rate.
        if "live" in meter_data:
            attributes["updated_from_usage_reports"] = meter_data["live"]
        for key in (
            "used_since_sign_in",
            "sign_in_units",
            "sign_in_balance",
            "sign_in_date",
            "rate",
            "rate_is_average",
            "balance_method",
            "price_layers",
            "price_now",
            "next_price",
            "units_at_price_now",
            "daily_use",
            "month_to_date",
            "bought_this_month",
            "last_top_up_time",
            "last_top_up_amount",
            "last_top_up_units",
            "approximate",
        ):
            if key in meter_data:
                attributes[key] = meter_data[key]
        if self._sensor_type == "remaining_balance" and meter_data.get("live"):
            attributes["estimated"] = True
        return attributes


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


class UZMZPlatformVersionSensor(CoordinatorEntity, SensorEntity):
    """The USMS website's platform version; a change usually means a site update."""

    _attr_has_entity_name = True
    _attr_name = "USMS Platform Version"
    _attr_icon = "mdi:web"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_unique_id = f"{DOMAIN}_integration_platform_version"

    @property
    def native_value(self) -> Optional[str]:
        """Return the version shown on the USMS home page."""
        return self.coordinator.platform_version

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the version this release was tested against."""
        return {"tested_version": KNOWN_PLATFORM_VERSION}

    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device info for the integration-status device."""
        return {
            "identifiers": {(DOMAIN, "integration")},
            "name": "UZMZ Integration",
            "manufacturer": "USMS",
            "model": "Integration Status",
        }
