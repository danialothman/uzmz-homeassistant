"""Config flow for UZMZ integration.

USMS logs in in two stages: IC number + password, then the same again plus a
captcha. Logging in is therefore always interactive: the user enters
credentials, the flow submits stage 1, then shows the captcha for stage 2.
The resulting session cookies are stored in the entry and reused by the
coordinator. When the session expires, the reauth flow repeats the login.
"""

import base64
import logging
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlowResult
from homeassistant.core import callback

from .api import (
    CaptchaChallenge,
    CannotConnect,
    InvalidCaptcha,
    InvalidCredentials,
    USMSClient,
    USMSError,
)
from .const import (
    DOMAIN,
    CONF_IC_NUMBER,
    CONF_PASSWORD,
    CONF_BASE_URL,
    CONF_SCAN_INTERVAL,
    CONF_SESSION_COOKIES,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

CONF_CAPTCHA = "captcha"

# Returned by _async_start_login when USMS logs in without asking for a captcha.
LOGGED_IN = "logged_in"


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for UZMZ."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: Dict[str, Any] = {}
        self._client: Optional[USMSClient] = None
        self._captcha_image: str = ""

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Collect credentials and submit the first login stage."""
        errors: Dict[str, str] = {}
        site_message = ""

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_IC_NUMBER])
            self._abort_if_unique_id_configured()
            self._data = user_input
            error, site_message = await self._async_start_login()
            if error is None:
                return await self.async_step_captcha()
            if error == LOGGED_IN:
                return self._async_finish()
            errors["base"] = error

        defaults = user_input or self._data
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_IC_NUMBER, default=defaults.get(CONF_IC_NUMBER, "")
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Optional(
                        CONF_BASE_URL, default=defaults.get(CONF_BASE_URL, DEFAULT_BASE_URL)
                    ): str,
                }
            ),
            errors=errors,
            description_placeholders={"site_message": site_message},
        )

    async def async_step_reauth(
        self, entry_data: Dict[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth when the stored USMS session has expired."""
        self._data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Wait for the user before contacting USMS.

        HA starts this flow as soon as the session expires, possibly hours
        before anyone opens it, so nothing is submitted until the user does.
        """
        errors: Dict[str, str] = {}
        site_message = ""

        if user_input is not None:
            if user_input.get(CONF_PASSWORD):
                self._data[CONF_PASSWORD] = user_input[CONF_PASSWORD]
            error, site_message = await self._async_start_login()
            if error is None:
                return await self.async_step_captcha()
            if error == LOGGED_IN:
                return self._async_finish()
            errors["base"] = error

        return self._show_reauth_form(errors, site_message)

    async def async_step_captcha(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Show the stage-2 captcha and complete the login."""
        errors: Dict[str, str] = {}
        site_message = ""

        if user_input is not None:
            error, site_message = await self._async_submit_captcha(
                user_input[CONF_CAPTCHA].strip()
            )
            if error is None:
                return self._async_finish()
            if error == "invalid_auth":
                # Password rejected: back to the step where it can be fixed.
                if self.source == SOURCE_REAUTH:
                    return self._show_reauth_form({"base": error}, site_message)
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema(
                        {
                            vol.Required(
                                CONF_IC_NUMBER, default=self._data[CONF_IC_NUMBER]
                            ): str,
                            vol.Required(CONF_PASSWORD): str,
                            vol.Optional(
                                CONF_BASE_URL,
                                default=self._data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
                            ): str,
                        }
                    ),
                    errors={"base": error},
                    description_placeholders={"site_message": site_message},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="captcha",
            data_schema=vol.Schema({vol.Required(CONF_CAPTCHA): str}),
            errors=errors,
            description_placeholders={
                "captcha_image": self._captcha_image,
                "site_message": site_message,
            },
        )

    def _show_reauth_form(
        self, errors: Dict[str, str], site_message: str
    ) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Optional(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={
                "ic_number": self._data[CONF_IC_NUMBER],
                "site_message": site_message,
            },
        )

    @callback
    def _async_finish(self) -> ConfigFlowResult:
        """Store the logged-in session on the entry."""
        cookies = self._client.get_cookies()
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data_updates={
                    CONF_PASSWORD: self._data[CONF_PASSWORD],
                    CONF_SESSION_COOKIES: cookies,
                },
            )
        return self.async_create_entry(
            title=f"UZMZ {self._data[CONF_IC_NUMBER]}",
            data={**self._data, CONF_SESSION_COOKIES: cookies},
        )

    async def _async_start_login(self) -> tuple[Optional[str], str]:
        """Open a fresh session and submit stage 1 (IC number + password).

        Returns (error, site_message). error is None when a captcha is ready
        in self._captcha_image, or LOGGED_IN if USMS didn't ask for one.
        """
        self._client = USMSClient(self._data.get(CONF_BASE_URL, DEFAULT_BASE_URL))
        try:
            challenge = await self.hass.async_add_executor_job(self._client.start_login)
            if challenge is None:
                challenge = await self.hass.async_add_executor_job(
                    self._client.submit_login,
                    self._data[CONF_IC_NUMBER],
                    self._data[CONF_PASSWORD],
                )
            if challenge is None:
                await self.hass.async_add_executor_job(self._client.fetch_meter_data)
                return LOGGED_IN, ""
        except USMSError as err:
            return _login_error(err)
        self._set_captcha(challenge)
        return None, ""

    async def _async_submit_captcha(self, captcha: str) -> tuple[Optional[str], str]:
        """Submit stage 2 and confirm meter data is readable."""
        try:
            challenge = await self.hass.async_add_executor_job(
                self._client.submit_login,
                self._data[CONF_IC_NUMBER],
                self._data[CONF_PASSWORD],
                captcha,
            )
            if challenge is None:
                await self.hass.async_add_executor_job(self._client.fetch_meter_data)
                return None, ""
            # USMS asked again instead of logging in: treat as a wrong code.
            self._set_captcha(challenge)
            return "invalid_captcha", ""
        except InvalidCaptcha:
            # The rejected page's form is spent; start over for a new captcha.
            error, site_message = await self._async_start_login()
            if error == LOGGED_IN:
                return None, ""
            return error or "invalid_captcha", site_message
        except USMSError as err:
            return _login_error(err)

    def _set_captcha(self, challenge: CaptchaChallenge) -> None:
        encoded = base64.b64encode(challenge.image).decode("ascii")
        self._captcha_image = f"data:{challenge.content_type};base64,{encoded}"

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler()


def _login_error(err: USMSError) -> tuple[str, str]:
    """Map a client error to a flow error key and USMS's own message."""
    if isinstance(err, InvalidCredentials):
        # USMS's text includes the attempts left before the account locks.
        return "invalid_auth", str(err)
    if isinstance(err, InvalidCaptcha):
        return "invalid_captcha", ""
    if isinstance(err, CannotConnect):
        return "cannot_connect", ""
    _LOGGER.warning("USMS login failed: %s", err)
    return "unknown", ""


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for the integration."""

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
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
