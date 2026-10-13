"""Data update coordinator for UZMZ integration."""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.components.persistent_notification import async_create, async_dismiss

from .api import InvalidCredentials, SessionExpired, USMSClient, USMSError
from .const import (
    AUTO_SIGN_IN_DELAY,
    CONF_AUTO_SIGN_IN_BLOCKED,
    CONF_BASE_URL,
    CONF_IC_NUMBER,
    CONF_PASSWORD,
    CONF_SAVE_PASSWORD,
    CONF_FRESH_SESSION,
    CONF_PLATFORM_VERSION,
    CONF_SESSION_COOKIES,
    CONF_USAGE_ANCHORS,
    CONF_USAGE_CHANGED,
    CONF_USAGE_RATES,
    CONF_USAGE_SEEN,
    DOMAIN,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    KEEPALIVE_INTERVAL,
    KNOWN_PLATFORM_VERSION,
)
from .usage import (
    ELECTRIC_TARIFF,
    WATER_TARIFF,
    current_readings,
    last_estimate,
    last_posted,
    learn_rate,
    make_anchor,
    new_session_start,
    parse_number,
    recent_usage,
    usage_changed,
    window_start,
)

_LOGGER = logging.getLogger(__name__)

NOTIFICATION_ID = "uzmz_update_failure"
# Fixed so automations can trigger on it (e.g. to push to a phone).
REAUTH_NOTIFICATION_ID = "uzmz_reauth_required"
PLATFORM_NOTIFICATION_ID = "uzmz_platform_changed"
AUTO_SIGN_IN_NOTIFICATION_ID = "uzmz_auto_sign_in"
MAX_FAILURES_BEFORE_NOTIFY = 3
FAILURE_RETRY_INTERVAL = timedelta(minutes=5)
BRUNEI_TZ = ZoneInfo("Asia/Brunei")


class UZMZDataUpdateCoordinator(DataUpdateCoordinator):
    """Fetch UZMZ data over a session that was logged in via the config flow.

    It reads pages with the stored session and keeps that session alive.
    When USMS ends the session it raises ConfigEntryAuthFailed, which starts
    the reauth flow where the user signs in (with a captcha if USMS asks).
    With a saved password it first tries once to sign in by itself, after
    AUTO_SIGN_IN_DELAY, and only falls back to reauth if that fails.
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
        # Automatic sign-in: one attempt per session (a success reloads the
        # entry, which starts a new coordinator).
        self._auto_sign_in_tried = False
        self._auto_sign_in_waiting = False
        self._auto_sign_in_error: Optional[str] = None
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
            if self._auto_sign_in_waiting or self._async_schedule_auto_sign_in():
                raise UpdateFailed(
                    "USMS session ended; UZMZ will sign in again automatically"
                ) from err
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
        self._async_check_platform_version(usage_state)
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
        seen = dict(entry_data.get(CONF_USAGE_SEEN) or {})
        changed = dict(entry_data.get(CONF_USAGE_CHANGED) or {})
        now = datetime.now(BRUNEI_TZ)
        today = now.date()
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
            start = new_session_start(today) if new_session else window_start(anchor, today)[0]
            try:
                usage_now = await self.hass.async_add_executor_job(
                    self.client.fetch_usage, path, start, today, meter == "electric"
                )
            except SessionExpired:
                raise
            except USMSError as err:
                _LOGGER.warning("Could not read USMS %s usage report: %s", meter, err)
                if not new_session:
                    readings.update(last_estimate((self.data or {}).get(meter)))
                readings["live"] = False
                unanchored = unanchored or new_session
                continue

            # When USMS last posted readings, to spot it falling behind.
            recent = recent_usage(usage_now, today)
            if usage_changed(seen.get(meter), recent):
                changed[meter] = now.isoformat()
            elif meter not in changed and (posted := last_posted(usage_now, now)):
                changed[meter] = posted
            seen[meter] = recent
            if meter in changed:
                readings["usage_updated"] = changed[meter]

            if new_session:
                if anchor and fresh:
                    rates[meter] = learn_rate(anchor, units, balance, rates.get(meter))
                anchor = make_anchor(
                    units, balance, today, usage_now,
                    approximate=(legacy or snapshot_changed) and not fresh,
                    start=start,
                )
                anchors[meter] = anchor

            # Both have published tariffs tiered by monthly use.
            tariff = {"electric": ELECTRIC_TARIFF, "water": WATER_TARIFF}.get(meter)
            current = current_readings(anchor, usage_now, start, rates.get(meter), tariff)
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
            CONF_USAGE_SEEN: seen,
            CONF_USAGE_CHANGED: changed,
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
    def _async_schedule_auto_sign_in(self) -> bool:
        """Start the wait before an automatic sign-in, if one is allowed."""
        entry = self.config_entry
        if (
            self._auto_sign_in_tried
            or not entry.options.get(CONF_SAVE_PASSWORD, False)
            or not entry.data.get(CONF_PASSWORD)
            or entry.data.get(CONF_AUTO_SIGN_IN_BLOCKED)
        ):
            return False
        self._auto_sign_in_waiting = True
        at = (datetime.now(BRUNEI_TZ) + AUTO_SIGN_IN_DELAY).strftime("%H:%M")
        _LOGGER.info("USMS session ended; signing in again automatically at %s", at)
        entry.async_on_unload(
            async_call_later(self.hass, AUTO_SIGN_IN_DELAY, self._async_auto_sign_in)
        )
        async_create(
            self.hass,
            (
                f"The USMS session ended, so readings are paused. UZMZ will sign in "
                f"again by itself at {at}. It waits in case you are using the USMS "
                "website or app: USMS allows one session per account, so signing in "
                "would end yours."
            ),
            title="UZMZ — Signing in again soon",
            notification_id=AUTO_SIGN_IN_NOTIFICATION_ID,
        )
        return True

    async def _async_auto_sign_in(self, _now: datetime) -> None:
        """Sign in once with the saved password; if that fails, ask the user."""
        self._auto_sign_in_tried = True
        self._auto_sign_in_waiting = False
        entry = self.config_entry
        client = USMSClient(entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL))
        try:
            signed_in = await self.hass.async_add_executor_job(
                client.sign_in, entry.data[CONF_IC_NUMBER], entry.data[CONF_PASSWORD]
            )
            error = None if signed_in else "USMS asked for a captcha"
        except InvalidCredentials as err:
            # Never retry a rejected password: 5 failures lock the account.
            error = f"USMS rejected the saved password ({err})"
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_AUTO_SIGN_IN_BLOCKED: True}
            )
        except USMSError as err:
            error = str(err)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected error signing in to USMS")
            error = f"Unexpected error: {err}"

        cookies = client.get_cookies()
        client.close()
        if error is None:
            _LOGGER.info("Signed in to USMS again automatically")
            async_create(
                self.hass,
                f"USMS ended the session, so UZMZ signed in again by itself at "
                f"{datetime.now(BRUNEI_TZ).strftime('%H:%M')}. Readings are back.",
                title="UZMZ — Signed in again",
                notification_id=AUTO_SIGN_IN_NOTIFICATION_ID,
            )
            self.hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, CONF_SESSION_COOKIES: cookies, CONF_FRESH_SESSION: True},
            )
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return

        _LOGGER.warning("Automatic USMS sign-in failed: %s", error)
        self._auto_sign_in_error = error
        async_dismiss(self.hass, AUTO_SIGN_IN_NOTIFICATION_ID)
        # The refresh hits SessionExpired again and starts the reauth flow.
        await self.async_refresh()

    @callback
    def _async_notify_reauth(self) -> None:
        """Tell the user to log in again; HA's reauth prompt alone is easy to miss."""
        tried = (
            f"UZMZ tried to sign in again by itself, but couldn't: {self._auto_sign_in_error}.\n\n"
            if self._auto_sign_in_error
            else ""
        )
        async_create(
            self.hass,
            (
                "The USMS session has ended, so meter readings have stopped updating. "
                "This happens whenever you sign in on the USMS website or app, "
                "because USMS allows one session per account.\n\n"
                f"{tried}"
                "Open [Settings → Devices & services](/config/integrations/integration/uzmz), "
                "choose **Reconfigure** on UZMZ and sign in again (solving the captcha if USMS shows one)."
            ),
            title="UZMZ — Sign-in required",
            notification_id=REAUTH_NOTIFICATION_ID,
        )

    @callback
    def async_set_scan_interval(self, interval: timedelta) -> None:
        """Apply a polling interval changed in the options flow."""
        if interval == self._normal_update_interval:
            return
        self._normal_update_interval = interval
        # In fast-retry mode after a failure, leave update_interval alone;
        # the next success restores the normal interval.
        if self._consecutive_failures == 0:
            self.update_interval = interval
        _LOGGER.info("Updated scan interval to %s", interval)

    @property
    def platform_version(self) -> Optional[str]:
        """USMS platform version from the last MainPage read."""
        return self.client.platform_version

    @callback
    def _async_check_platform_version(self, state: Dict[str, Any]) -> None:
        """Warn once when the USMS platform version changes; add it to state."""
        current = self.client.platform_version
        if not current:
            return
        previous = self.config_entry.data.get(CONF_PLATFORM_VERSION, KNOWN_PLATFORM_VERSION)
        state[CONF_PLATFORM_VERSION] = current
        if current == previous:
            return
        _LOGGER.warning("USMS platform version changed from %s to %s", previous, current)
        async_create(
            self.hass,
            (
                f"USMS updated its website from version {previous} to {current}.\n\n"
                "UZMZ is still reading your meters. If readings stop or look wrong, "
                "check for a UZMZ update in HACS, or report it on "
                "[GitHub](https://github.com/danialothman/uzmz-homeassistant/issues)."
            ),
            title="UZMZ — USMS website updated",
            notification_id=PLATFORM_NOTIFICATION_ID,
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
