"""Data update coordinator for UZMZ integration."""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.components.persistent_notification import async_create, async_dismiss

from .api import SessionExpired, USMSClient, USMSError
from .const import (
    CONF_SESSION_COOKIES,
    DOMAIN,
    DEFAULT_SCAN_INTERVAL,
    KEEPALIVE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

NOTIFICATION_ID = "uzmz_update_failure"
# Fixed so automations can trigger on it (e.g. to push to a phone).
REAUTH_NOTIFICATION_ID = "uzmz_reauth_required"
MAX_FAILURES_BEFORE_NOTIFY = 3
FAILURE_RETRY_INTERVAL = timedelta(minutes=5)
BRUNEI_TZ = ZoneInfo("Asia/Brunei")


class UZMZDataUpdateCoordinator(DataUpdateCoordinator):
    """Fetch UZMZ data over a session that was logged in via the config flow.

    USMS requires a captcha to log in, so the coordinator never logs in itself.
    It only reads pages with the stored session and keeps that session alive;
    when USMS ends the session it raises ConfigEntryAuthFailed, which starts
    the reauth flow where the user solves a new captcha.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: USMSClient,
        update_interval: timedelta = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        """Initialize the data updater."""
        self.client = client
        self._consecutive_failures = 0
        self._normal_update_interval = update_interval
        self._notification_active = False
        self._session_expired = False
        self.last_successful_refresh: Optional[datetime] = None
        self.last_attempt_time: Optional[datetime] = None

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=update_interval,
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from UZMZ website."""
        self.last_attempt_time = datetime.now(BRUNEI_TZ)
        try:
            data = await self.hass.async_add_executor_job(self.client.fetch_meter_data)
        except SessionExpired as err:
            self._session_expired = True
            self._async_notify_reauth()
            raise ConfigEntryAuthFailed(
                "USMS session expired; re-authenticate and solve the captcha"
            ) from err
        except USMSError as err:
            await self._async_handle_failure(str(err))
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected error fetching UZMZ data")
            await self._async_handle_failure(f"Unexpected error: {err}")
            raise UpdateFailed(f"Error fetching data: {err}") from err

        self._session_expired = False
        async_dismiss(self.hass, REAUTH_NOTIFICATION_ID)
        if self._consecutive_failures > 0:
            _LOGGER.info(
                "UZMZ update succeeded after %d consecutive failure(s)",
                self._consecutive_failures,
            )
            self._consecutive_failures = 0
        if self._notification_active:
            async_dismiss(self.hass, NOTIFICATION_ID)
            self._notification_active = False
        self.last_successful_refresh = datetime.now(BRUNEI_TZ)
        if self.update_interval != self._normal_update_interval:
            _LOGGER.info(
                "Restored normal polling interval (%s) after recovery",
                self._normal_update_interval,
            )
            self.update_interval = self._normal_update_interval
        self._async_persist_cookies()
        return data

    async def _async_handle_failure(self, reason: str) -> None:
        """Count a transient failure, switch to fast retry and notify if it persists."""
        self._consecutive_failures += 1
        _LOGGER.warning(
            "UZMZ update failed (%d consecutive): %s",
            self._consecutive_failures,
            reason,
        )
        if self.update_interval != FAILURE_RETRY_INTERVAL:
            _LOGGER.info(
                "Switched to fast retry interval (%s) after failure",
                FAILURE_RETRY_INTERVAL,
            )
            self.update_interval = FAILURE_RETRY_INTERVAL
        if self._consecutive_failures >= MAX_FAILURES_BEFORE_NOTIFY:
            async_create(
                self.hass,
                (
                    f"UZMZ has failed to update {self._consecutive_failures} times in a row.\n\n"
                    f"**Reason:** {reason}\n\n"
                    "The integration will keep retrying automatically. "
                    "If this persists, check your internet connection or the USMS website."
                ),
                title="UZMZ Utility Meter — Update Failure",
                notification_id=NOTIFICATION_ID,
            )
            self._notification_active = True

    @callback
    def _async_notify_reauth(self) -> None:
        """Tell the user to log in again; HA's reauth prompt alone is easy to miss."""
        async_create(
            self.hass,
            (
                f"The USMS session for **{self.config_entry.title}** has expired, "
                "so meter readings have stopped updating.\n\n"
                "Open [Settings → Devices & services](/config/integrations/integration/uzmz), "
                "choose **Reconfigure** on UZMZ and solve the captcha to sign in again."
            ),
            title="UZMZ — Sign-in required",
            notification_id=REAUTH_NOTIFICATION_ID,
        )

    @callback
    def _async_persist_cookies(self) -> None:
        """Save the session cookies so a restart can reuse the logged-in session."""
        cookies = self.client.get_cookies()
        if cookies != self.config_entry.data.get(CONF_SESSION_COOKIES):
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_SESSION_COOKIES: cookies},
            )

    @callback
    def async_start_keepalive(self) -> CALLBACK_TYPE:
        """Ping USMS periodically so the session outlives the polling interval."""
        return async_track_time_interval(
            self.hass, self._async_keepalive, KEEPALIVE_INTERVAL
        )

    async def _async_keepalive(self, _now: datetime) -> None:
        if self._session_expired:
            return
        try:
            await self.hass.async_add_executor_job(self.client.keep_alive)
        except SessionExpired:
            _LOGGER.warning("USMS session expired; requesting re-authentication")
            # The refresh hits SessionExpired too and starts the reauth flow.
            await self.async_refresh()
        except USMSError as err:
            _LOGGER.debug("USMS keep-alive failed: %s", err)
