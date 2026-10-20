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
    CONF_AUTO_SIGN_IN_TRIED,
    CONF_BASE_URL,
    CONF_IC_NUMBER,
    CONF_PASSWORD,
    CONF_SAVE_PASSWORD,
    CONF_SESSION_ENDED,
    CONF_FRESH_SESSION,
    CONF_PLATFORM_VERSION,
    CONF_SESSION_COOKIES,
    CONF_TOP_UPS,
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
    average_daily_use,
    current_readings,
    infer_layers,
    last_estimate,
    last_posted,
    learn_rate,
    make_anchor,
    month_top_ups,
    new_session_start,
    next_price_change,
    parse_number,
    recent_usage,
    settle_layers,
    top_up_advice,
    top_up_plan,
    usage_changed,
    window_start,
)

_LOGGER = logging.getLogger(__name__)

NOTIFICATION_ID = "uzmz_update_failure"
# Fixed so automations can trigger on it (e.g. to push to a phone).
REAUTH_NOTIFICATION_ID = "uzmz_reauth_required"
PLATFORM_NOTIFICATION_ID = "uzmz_platform_changed"
AUTO_SIGN_IN_NOTIFICATION_ID = "uzmz_auto_sign_in"
UNSUPPORTED_METERS_NOTIFICATION_ID = "uzmz_unsupported_meters"
ISSUES_URL = "https://github.com/danialothman/uzmz-homeassistant/issues"
MAX_FAILURES_BEFORE_NOTIFY = 3
FAILURE_RETRY_INTERVAL = timedelta(minutes=5)
BRUNEI_TZ = ZoneInfo("Asia/Brunei")


class UZMZDataUpdateCoordinator(DataUpdateCoordinator):
    """Fetch UZMZ data over a session that was logged in via the config flow.

    It reads pages with the stored session and keeps that session alive.
    When USMS ends the session it raises ConfigEntryAuthFailed, which starts
    the reauth flow where the user signs in (with a captcha if USMS asks).
    With a saved password it first tries once to sign in by itself,
    AUTO_SIGN_IN_DELAY after the session ended, and only falls back to reauth
    if that fails.
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
        self._auto_sign_in_timer: Optional[CALLBACK_TYPE] = None
        self._auto_sign_in_error: Optional[str] = None
        self._meters_checked = False
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
            try:
                data, usage_state = await self._async_fetch()
            except SessionExpired:
                if not await self._async_auto_sign_in():
                    raise
                data, usage_state = await self._async_fetch()
        except SessionExpired as err:
            self._session_expired = True
            self._async_notify_reauth()
            raise ConfigEntryAuthFailed(
                "USMS session expired; re-authenticate and solve the captcha"
            ) from err
        except USMSError as err:
            await self._async_handle_failure(str(err))
            raise UpdateFailed(str(err)) from err
        except UpdateFailed:
            raise  # waiting to sign in again
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
        self._async_check_meter_counts()
        entry_data = self.config_entry.data
        if entry_data.get(CONF_SESSION_ENDED) or entry_data.get(CONF_AUTO_SIGN_IN_TRIED):
            # The session works, so a stale end time mustn't skip the next wait.
            self._async_update_entry_data({CONF_SESSION_ENDED: None, CONF_AUTO_SIGN_IN_TRIED: None})
        self._async_persist(usage_state)
        return data

    async def _async_fetch(self) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Read the meters and bring them up to date from the usage reports."""
        data = await self.hass.async_add_executor_job(self.client.fetch_meter_data)
        return data, await self._async_apply_live_usage(data)

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
        top_ups = dict(entry_data.get(CONF_TOP_UPS) or {})
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
            # Both have published tariffs, applied to each month's top-ups.
            tariff = {"electric": ELECTRIC_TARIFF, "water": WATER_TARIFF}.get(meter)
            unit = "kWh" if meter == "electric" else "m³"
            if tariff:
                top_ups[meter] = month_top_ups(top_ups.get(meter), today)
                readings.update(top_up_plan(top_ups[meter], tariff))
                readings["top_up_advice"] = top_up_advice(top_ups[meter], tariff, unit)
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
                layers = None
                if tariff:
                    # Price what's left: usage since the last sign-in comes off
                    # the old layers, and a top-up adds new ones.
                    used = (
                        current_readings(anchor, usage_now, start, None, tariff)["used_since_sign_in"]
                        if anchor
                        else 0.0
                    )
                    layers, top_ups[meter], topped_up = settle_layers(
                        anchor, units, balance, used, tariff, top_ups[meter]
                    )
                    if topped_up is not None:
                        _LOGGER.info("USMS %s top-up of B$%.2f seen at sign-in", meter, topped_up)
                    readings.update(top_up_plan(top_ups[meter], tariff))
                    readings["top_up_advice"] = top_up_advice(top_ups[meter], tariff, unit)
                anchor = make_anchor(
                    units, balance, today, usage_now,
                    approximate=(legacy or snapshot_changed) and not fresh,
                    start=start,
                    layers=layers,
                )
                anchors[meter] = anchor
            elif tariff and "layers" not in anchor:
                # An anchor from before 1.8.0: price its units once.
                anchor = {**anchor, "layers": infer_layers(anchor["units"], anchor["balance"], tariff)}
                anchors[meter] = anchor

            current = current_readings(anchor, usage_now, start, rates.get(meter), tariff)
            readings.update(current)
            if tariff:
                readings.update(
                    next_price_change(
                        current["price_layers"], average_daily_use(usage_now, today), today
                    )
                )
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
            CONF_TOP_UPS: top_ups,
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

    async def _async_auto_sign_in(self) -> bool:
        """Sign in again with the saved password, once per ended session.

        Returns True once signed in (self.client then holds the new session),
        or False if it isn't allowed or failed, so the user is asked instead.
        Raises UpdateFailed while waiting for AUTO_SIGN_IN_DELAY to pass.
        """
        entry = self.config_entry
        if (
            not entry.options.get(CONF_SAVE_PASSWORD, False)
            or not entry.data.get(CONF_PASSWORD)
            or entry.data.get(CONF_AUTO_SIGN_IN_BLOCKED)
            or entry.data.get(CONF_AUTO_SIGN_IN_TRIED)
        ):
            return False

        now = datetime.now(BRUNEI_TZ)
        ended = entry.data.get(CONF_SESSION_ENDED)
        if not ended:
            ended = now.isoformat()
            self._async_update_entry_data({CONF_SESSION_ENDED: ended})
        due = datetime.fromisoformat(ended) + AUTO_SIGN_IN_DELAY
        if now < due:
            # Stop the keep-alive until then; it would only find the same.
            self._session_expired = True
            if self._auto_sign_in_timer is None:
                _LOGGER.info(
                    "USMS session ended; signing in again automatically at %s",
                    due.strftime("%H:%M"),
                )
                self._auto_sign_in_timer = async_call_later(
                    self.hass, due - now, self._async_sign_in_when_due
                )
                entry.async_on_unload(self._async_cancel_auto_sign_in)
                async_create(
                    self.hass,
                    (
                        f"The USMS session ended, so readings are paused. UZMZ will sign in "
                        f"again by itself at {due.strftime('%H:%M')}. It waits in case you are "
                        "using the USMS website or app: USMS allows one session per account, "
                        "so signing in would end yours."
                    ),
                    title="UZMZ — Signing in again soon",
                    notification_id=AUTO_SIGN_IN_NOTIFICATION_ID,
                )
            raise UpdateFailed(
                f"USMS session ended; signing in again automatically at {due.strftime('%H:%M')}"
            )

        # The one attempt for this session, even across restarts.
        self._async_update_entry_data({CONF_AUTO_SIGN_IN_TRIED: True})
        async_dismiss(self.hass, AUTO_SIGN_IN_NOTIFICATION_ID)
        client = USMSClient(entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL))
        try:
            signed_in = await self.hass.async_add_executor_job(
                client.sign_in, entry.data[CONF_IC_NUMBER], entry.data[CONF_PASSWORD]
            )
            error = None if signed_in else "USMS asked for a captcha"
        except InvalidCredentials as err:
            # Never retry a rejected password: 5 failures lock the account.
            error = f"USMS rejected the saved password ({err})"
            self._async_update_entry_data({CONF_AUTO_SIGN_IN_BLOCKED: True})
        except USMSError as err:
            error = str(err)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected error signing in to USMS")
            error = f"Unexpected error: {err}"

        if error is not None:
            client.close()
            _LOGGER.warning("Automatic USMS sign-in failed: %s", error)
            self._auto_sign_in_error = error
            return False

        _LOGGER.info("Signed in to USMS again automatically")
        old, self.client = self.client, client
        old.close()
        entry.async_on_unload(client.close)
        # A new session: re-anchor the usage tracking, as after a manual sign-in.
        data = {
            key: value
            for key, value in entry.data.items()
            if key not in (CONF_SESSION_ENDED, CONF_AUTO_SIGN_IN_TRIED)
        }
        data.update({CONF_SESSION_COOKIES: client.get_cookies(), CONF_FRESH_SESSION: True})
        self.hass.config_entries.async_update_entry(entry, data=data)
        self._session_expired = False
        async_create(
            self.hass,
            f"USMS ended the session, so UZMZ signed in again by itself at "
            f"{datetime.now(BRUNEI_TZ).strftime('%H:%M')}. Readings are back.",
            title="UZMZ — Signed in again",
            notification_id=AUTO_SIGN_IN_NOTIFICATION_ID,
        )
        return True

    async def _async_sign_in_when_due(self, _now: datetime) -> None:
        self._auto_sign_in_timer = None
        await self.async_refresh()

    @callback
    def _async_cancel_auto_sign_in(self) -> None:
        if self._auto_sign_in_timer is not None:
            self._auto_sign_in_timer()
            self._auto_sign_in_timer = None

    @callback
    def _async_update_entry_data(self, updates: Dict[str, Any]) -> None:
        entry = self.config_entry
        self.hass.config_entries.async_update_entry(entry, data={**entry.data, **updates})

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
    def _async_check_meter_counts(self) -> None:
        """Say once per start if the account has meters UZMZ can't show."""
        if self._meters_checked:
            return
        self._meters_checked = True
        counts = self.client.meter_counts
        extra = [
            f"{counts[kind]} {kind} meters" if kind != "other" else f"{counts[kind]} meter(s) of another type"
            for kind in ("electric", "water", "other")
            if counts.get(kind, 0) > (0 if kind == "other" else 1)
        ]
        if not extra:
            return
        _LOGGER.warning(
            "USMS account has %s; UZMZ only supports one electric and one water meter",
            ", ".join(extra),
        )
        async_create(
            self.hass,
            (
                f"Your USMS account has {', '.join(extra)}. UZMZ currently supports one "
                "electric and one water meter per account, so it only shows the first "
                "of each type and ignores the rest.\n\n"
                f"If you'd like the others supported, please [open an issue]({ISSUES_URL}) "
                "and, if you can, attach a screenshot of the USMS home page with your "
                "name, IC number and account details blurred."
            ),
            title="UZMZ — Some meters aren't shown",
            notification_id=UNSUPPORTED_METERS_NOTIFICATION_ID,
        )

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
                f"[GitHub]({ISSUES_URL})."
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
