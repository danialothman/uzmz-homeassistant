"""Data update coordinator for UZMZ integration."""

import logging
from datetime import timedelta
from typing import Any, Dict, Optional

import requests
from bs4 import BeautifulSoup
import urllib.parse

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.components.persistent_notification import async_create, async_dismiss

from .const import (
    DOMAIN,
    FORM_VIEWSTATE,
    FORM_EVENTVALIDATION,
    FORM_VIEWSTATEGENERATOR,
    FORM_USERNAME,
    FORM_PASSWORD,
    FORM_LOGIN_BUTTON,
    FORM_LOGOUT_BUTTON,
    ELEM_USERNAME_SPAN,
    ELEM_VERSION_SPAN,
    ELEM_IFRAME,
    FIELD_MAPPINGS,
    IMG_ELECTRIC,
    IMG_WATER,
    DEFAULT_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

NOTIFICATION_ID = "uzmz_update_failure"
MAX_FAILURES_BEFORE_NOTIFY = 3


class UZMZDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching UZMZ data from the website."""

    def __init__(
        self,
        hass: HomeAssistant,
        ic_number: str,
        password: str,
        base_url: str,
        platform_version: Optional[str] = None,
        update_interval: timedelta = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        """Initialize the data updater."""
        self.ic_number = ic_number
        self.password = password
        self.base_url = base_url
        self.platform_version = platform_version
        self.session = requests.Session()
        self.login_url = base_url + "ResLogin"
        self._consecutive_failures = 0

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )

    def _reset_session(self) -> None:
        """Create a fresh session to avoid stale cookies/connections."""
        try:
            self.session.close()
        except Exception:
            pass
        self.session = requests.Session()

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from UZMZ website."""
        return await self.hass.async_add_executor_job(self._fetch_data)

    def _fetch_data(self) -> Dict[str, Any]:
        """Fetch meter data from the website."""
        try:
            # Login to the website
            login_result = self._login()

            if login_result["status"] == "auth_failed":
                raise ConfigEntryAuthFailed("Login failed. Please check credentials.")

            if login_result["status"] == "connection_error":
                raise UpdateFailed(
                    f"Could not connect to USMS: {login_result['error']}"
                )

            if login_result["status"] == "site_error":
                raise UpdateFailed(
                    f"USMS site may be down or under maintenance: {login_result['error']}"
                )

            response = login_result["response"]

            # Get iframe content
            iframe_response = self._get_iframe_content(response)
            if not iframe_response:
                raise UpdateFailed("Failed to retrieve meter data from USMS")

            # Parse meter data
            meter_data = self._filter_iframe_content(iframe_response)

            # Logout
            self._logout()

            # Format data for sensors
            formatted_data = self._format_meter_data(meter_data)

            # Success — reset failure count and dismiss any notification
            if self._consecutive_failures > 0:
                _LOGGER.info(
                    "UZMZ update succeeded after %d consecutive failure(s)",
                    self._consecutive_failures,
                )
                self._consecutive_failures = 0
                async_dismiss(self.hass, NOTIFICATION_ID)

            return formatted_data

        except ConfigEntryAuthFailed:
            self._consecutive_failures += 1
            raise
        except UpdateFailed as err:
            self._consecutive_failures += 1
            self._handle_failure(str(err))
            # Reset session for next attempt
            self._reset_session()
            raise
        except Exception as err:
            self._consecutive_failures += 1
            self._handle_failure(f"Unexpected error: {err}")
            self._reset_session()
            _LOGGER.error("Error fetching UZMZ data: %s", err)
            raise UpdateFailed(f"Error fetching data: {err}")
        finally:
            # Clear session cookies
            self.session.cookies.clear()

    def _handle_failure(self, reason: str) -> None:
        """Track failures and notify user after repeated failures."""
        _LOGGER.warning(
            "UZMZ update failed (%d consecutive): %s",
            self._consecutive_failures,
            reason,
        )
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

    def _login(self) -> Dict[str, Any]:
        """Login to the UZMZ website.

        Returns a dict with:
          - status: "success", "auth_failed", "connection_error", or "site_error"
          - response: the Response object (on success)
          - error: error description (on failure)
        """
        try:
            # Get login page for tokens
            response = self.session.get(self.login_url, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            # Extract hidden fields
            viewstate = soup.find("input", {"name": FORM_VIEWSTATE})
            eventvalidation = soup.find("input", {"name": FORM_EVENTVALIDATION})
            viewstategenerator = soup.find("input", {"name": FORM_VIEWSTATEGENERATOR})

            if not all([viewstate, eventvalidation, viewstategenerator]):
                _LOGGER.error("Login page missing form tokens — site may be under maintenance")
                return {
                    "status": "site_error",
                    "error": "Login page did not contain expected form fields",
                }

            # Prepare login data
            login_data = {
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                FORM_VIEWSTATE: viewstate["value"],
                FORM_VIEWSTATEGENERATOR: viewstategenerator["value"],
                FORM_EVENTVALIDATION: eventvalidation["value"],
                FORM_USERNAME: self.ic_number,
                FORM_PASSWORD: self.password,
                FORM_LOGIN_BUTTON: "Login",
            }

            # Perform login
            response = self.session.post(self.login_url, data=login_data, timeout=30)
            response.raise_for_status()

            # Check login success
            soup = BeautifulSoup(response.text, "html.parser")
            login_success_msg = soup.find("span", {"id": ELEM_USERNAME_SPAN})

            if not login_success_msg:
                _LOGGER.warning("Login failed. Incorrect credentials.")
                return {"status": "auth_failed", "error": "Invalid credentials"}

            # Check platform version if provided
            if self.platform_version:
                version_elem = soup.find("span", {"id": ELEM_VERSION_SPAN})
                if version_elem:
                    actual_version = version_elem.get_text(strip=True)
                    if actual_version != self.platform_version:
                        _LOGGER.warning(
                            "Platform version mismatch. Expected: %s, Got: %s",
                            self.platform_version,
                            actual_version
                        )

            _LOGGER.info("Login successful")
            return {"status": "success", "response": response}

        except requests.ConnectionError as err:
            _LOGGER.error("Connection error during login: %s", err)
            return {
                "status": "connection_error",
                "error": f"Cannot reach USMS website: {err}",
            }
        except requests.Timeout as err:
            _LOGGER.error("Timeout during login: %s", err)
            return {
                "status": "connection_error",
                "error": f"USMS website timed out: {err}",
            }
        except requests.HTTPError as err:
            status_code = err.response.status_code if err.response is not None else "unknown"
            _LOGGER.error("HTTP error during login: %s (status %s)", err, status_code)
            return {
                "status": "site_error",
                "error": f"USMS returned HTTP {status_code}",
            }
        except requests.RequestException as err:
            _LOGGER.error("Request error during login: %s", err)
            return {
                "status": "connection_error",
                "error": str(err),
            }

    def _get_iframe_content(self, response: requests.Response) -> Optional[requests.Response]:
        """Get the iframe content containing meter data."""
        try:
            soup = BeautifulSoup(response.text, "html.parser")
            iframe = soup.find("iframe", {"id": ELEM_IFRAME})

            if iframe and iframe.get("src"):
                iframe_url = urllib.parse.urljoin(self.base_url, iframe["src"])
                iframe_response = self.session.get(iframe_url, timeout=30)
                iframe_response.raise_for_status()
                return iframe_response
            else:
                _LOGGER.warning("iFrame not found or src attribute missing")
                return None

        except requests.RequestException as err:
            _LOGGER.error("Error retrieving iframe content: %s", err)
            return None

    def _filter_iframe_content(self, iframe_response: requests.Response) -> list[Dict[str, str]]:
        """Parse meter data from iframe content."""
        soup = BeautifulSoup(iframe_response.text, "html.parser")
        cards = soup.find_all("td", class_="dxcvCard")

        filtered_data = []

        for card in cards:
            meter_data = {}

            # Determine meter type
            meter_type_img = card.find(
                "img",
                id=lambda x: x and x.startswith("ASPxCardView1_DXCardLayout"),
            )
            if meter_type_img:
                if meter_type_img["src"] == IMG_ELECTRIC:
                    meter_data["Meter Type"] = "Electric"
                elif meter_type_img["src"] == IMG_WATER:
                    meter_data["Meter Type"] = "Water"

            # Extract field values
            for field_name, id_func in FIELD_MAPPINGS:
                span = card.find("span", id=id_func)
                if span:
                    value = span.find_next(
                        "td", class_="dxflNestedControlCell"
                    ).text.strip()
                    meter_data[field_name] = value

            if meter_data:
                filtered_data.append(meter_data)

        _LOGGER.info("Filtered %d meter entries", len(filtered_data))
        return filtered_data

    def _logout(self) -> None:
        """Logout from the UZMZ website."""
        try:
            response = self.session.get(self.base_url, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            form = soup.find("form")
            if not form:
                _LOGGER.warning("Logout form not found")
                return

            form_data = {}
            for input_tag in form.find_all("input"):
                if input_tag.get("name"):
                    form_data[input_tag["name"]] = input_tag.get("value", "")

            form_data[FORM_LOGOUT_BUTTON] = "Logout"

            form_action = form.get("action", "")
            logout_url = urllib.parse.urljoin(self.base_url, form_action)

            response = self.session.post(logout_url, data=form_data, timeout=30)
            response.raise_for_status()
            _LOGGER.info("Logged out successfully")

        except requests.RequestException as err:
            _LOGGER.error("Error during logout: %s", err)

    def _format_meter_data(self, meter_data: list[Dict[str, str]]) -> Dict[str, Any]:
        """Format meter data for sensor entities."""
        formatted = {
            "electric": {},
            "water": {},
        }

        for meter in meter_data:
            meter_type = meter.get("Meter Type", "").lower()
            if meter_type in formatted:
                formatted[meter_type] = {
                    "status": meter.get("Meter Status", "Unknown"),
                    "remaining_unit": meter.get("Remaining Unit", "0"),
                    "remaining_balance": meter.get("Remaining Balance", "0"),
                    "last_updated": meter.get("Last Updated", ""),
                }

        return formatted
