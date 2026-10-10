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
    CONF_FRESH_SESSION,
    CONF_SESSION_COOKIES,
    CONF_USAGE_ANCHORS,
    CONF_USAGE_RATES,
    DOMAIN,
    DEFAULT_SCAN_INTERVAL,
    KEEPALIVE_INTERVAL,
)
from .usage import (
    LOOKBACK_DAYS,
    current_readings,
    learn_rate,
    make_anchor,
    parse_number,
    window_start,
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
            usage_state = await self._async_apply_live_usage(data)
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
        self._async_persist(usage_state)
        return data

    async def _async_apply_live_usage(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Replace the login-time readings in data with current ones.

        Returns the anchors/rates to store. Raises SessionExpired; other
        report failures leave that meter's login-time readings in place.
        """
        entry_data = self.config_entry.data
        fresh = entry_data.get(CONF_FRESH_SESSION, False)
        # Entries from before 1.3.0 have no anchors and a session of unknown age.
        legacy = CONF_USAGE_ANCHORS not in entry_data
        anchors = dict(entry_data.get(CONF_USAGE_ANCHORS) or {})
        rates = dict(entry_data.get(CONF_USAGE_RATES) or {})
        today = datetime.now(BRUNEI_TZ).date()
        # Keep the fresh-login flag until every meter has been anchored.
        unanchored = False

        for meter, readings in data.items():
            units = parse_number(readings.get("remaining_unit"))
            balance = parse_number(readings.get("remaining_balance"))
            path = readings.get("usage_path")
            if units is None or balance is None or not path:
                continue
            anchor = anchors.get(meter)
            # USMS only changes the Home snapshot at login, so a changed
            # snapshot without a flow login means a session we didn't see start.
            snapshot_changed = bool(anchor) and (
                anchor["units"] != units or anchor["balance"] != balance
            )
            new_session = fresh or anchor is None or snapshot_changed
            start = (
                today - timedelta(days=LOOKBACK_DAYS)
                if new_session
                else window_start(anchor, today)[0]
            )
            try:
                usage_now = await self.hass.async_add_executor_job(
                    self.client.fetch_usage, path, start, today, meter == "electric"
                )
            except SessionExpired:
                raise
            except USMSError as err:
                _LOGGER.warning("Could not read USMS %s usage report: %s", meter, err)
                readings["live"] = False
                unanchored = unanchored or new_session
                continue

            if new_session:
                if anchor and fresh:
                    rates[meter] = learn_rate(anchor, units, balance, rates.get(meter))
                anchor = make_anchor(
                    units, balance, today, usage_now,
                    approximate=(legacy or snapshot_changed) and not fresh,
                )
                anchors[meter] = anchor

            current = current_readings(anchor, usage_now, start, rates.get(meter))
            readings.update(current)
            readings.update(
                {
                    "live": True,
                    "sign_in_units": anchor["units"],
                    "sign_in_balance": anchor["balance"],
                    "sign_in_date": anchor["date"],
                    "approximate": anchor["approximate"] or window_start(anchor, today)[1],
                }
            )

        return {
            CONF_USAGE_ANCHORS: anchors,
            CONF_USAGE_RATES: rates,
            CONF_FRESH_SESSION: fresh and unanchored,
        }

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
                "The USMS session has expired, so meter readings have stopped updating.\n\n"
                "Open [Settings → Devices & services](/config/integrations/integration/uzmz), "
                "choose **Reconfigure** on UZMZ and solve the captcha to sign in again."
            ),
            title="UZMZ — Sign-in required",
            notification_id=REAUTH_NOTIFICATION_ID,
        )

    @callback
    def _async_persist(self, usage_state: Dict[str, Any]) -> None:
        """Save cookies and usage anchors so a restart can carry on the session."""
        updates = {CONF_SESSION_COOKIES: self.client.get_cookies(), **usage_state}
        data = self.config_entry.data
        if any(data.get(key) != value for key, value in updates.items()):
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**data, **updates}
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
