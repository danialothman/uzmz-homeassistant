"""Config flow for UZMZ integration."""

import logging
from typing import Any, Dict, Optional

import voluptuous as vol
import requests

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

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


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


async def validate_input(hass: HomeAssistant, data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the user input allows us to connect."""
    
    ic_number = data[CONF_IC_NUMBER]
    password = data[CONF_PASSWORD]
    base_url = data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
    platform_version = data.get(CONF_PLATFORM_VERSION)
    
    # Test the connection
    coordinator = UZMZDataUpdateCoordinator(
        hass,
        ic_number=ic_number,
        password=password,
        base_url=base_url,
        platform_version=platform_version,
    )
    
    try:
        # Try to fetch data
        await hass.async_add_executor_job(coordinator._fetch_data)
    except Exception as err:
        if "Login failed" in str(err):
            raise InvalidAuth from err
        raise CannotConnect from err
    
    # Return info to store in the config entry
    return {"title": f"UZMZ {ic_number}"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for UZMZ."""

    VERSION = 1

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_IC_NUMBER): str,
                        vol.Required(CONF_PASSWORD): str,
                        vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                        vol.Optional(CONF_PLATFORM_VERSION): str,
                    }
                ),
            )

        errors = {}

        try:
            info = await validate_input(self.hass, user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            # Check if already configured
            await self.async_set_unique_id(user_input[CONF_IC_NUMBER])
            self._abort_if_unique_id_configured()
            
            return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_IC_NUMBER, default=user_input.get(CONF_IC_NUMBER, "")
                    ): str,
                    vol.Required(
                        CONF_PASSWORD, default=user_input.get(CONF_PASSWORD, "")
                    ): str,
                    vol.Optional(
                        CONF_BASE_URL, default=user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL)
                    ): str,
                    vol.Optional(
                        CONF_PLATFORM_VERSION, default=user_input.get(CONF_PLATFORM_VERSION, "")
                    ): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for the integration."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Get current values
        scan_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            DEFAULT_SCAN_INTERVAL.total_seconds()
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=int(scan_interval),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=300, max=86400),  # 5 minutes to 24 hours
                    ),
                }
            ),
        )