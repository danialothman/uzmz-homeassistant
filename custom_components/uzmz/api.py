"""USMS website client for the UZMZ integration.

USMS logs in in two stages: IC number + password, then (on ResLogin?sc=...)
IC number + password + a DevExpress captcha. So this client never logs in
unattended. A login happens only from the config/reauth flow, where the user
solves the captcha; afterwards the coordinator reuses the authenticated
ASP.NET session (cookies) for read-only GETs and keeps it alive.

Never GET the base URL or ResLogin with a live session: USMS ends the session
whenever the login page is served.
"""

import logging
import threading
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from bs4 import BeautifulSoup

from .const import (
    ELEM_CAPTCHA_BOX,
    ELEM_CAPTCHA_ERROR,
    ELEM_CAPTCHA_IMG,
    ELEM_IFRAME,
    ELEM_LOGIN_ERROR,
    ELEM_USERNAME_SPAN,
    FIELD_MAPPINGS,
    FORM_CAPTCHA,
    FORM_LOGIN_BUTTON,
    FORM_PASSWORD,
    FORM_USERNAME,
    FORM_VIEWSTATE,
    IMG_ELECTRIC,
    IMG_WATER,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


class USMSError(Exception):
    """Base error for USMS client failures."""


class CannotConnect(USMSError):
    """USMS could not be reached."""


class SiteError(USMSError):
    """USMS returned something unexpected (maintenance, layout change)."""


class InvalidCaptcha(USMSError):
    """The submitted captcha code was rejected."""


class InvalidCredentials(USMSError):
    """USMS rejected the IC number/password. Message is the site's own text."""


class SessionExpired(USMSError):
    """The stored session is no longer logged in; a new captcha login is needed."""


@dataclass
class CaptchaChallenge:
    """A captcha image tied to the client's current session."""

    image: bytes
    content_type: str


class USMSClient:
    """Blocking USMS client. Call its methods from an executor thread."""

    def __init__(
        self,
        base_url: str,
        cookies: Optional[Dict[str, str]] = None,
    ) -> None:
        self.base_url = base_url
        self.login_url = base_url + "ResLogin"
        # Logged-in landing page. Never GET the base URL or ResLogin with a
        # live session: USMS serves the login page there and ends the session.
        self.main_url = base_url + "MainPage"
        self.session = requests.Session()
        # Restore under the site's host so cookies the server sets later
        # (USMS issues a new session id at login) replace these instead of
        # sitting alongside them as duplicates.
        host = urllib.parse.urlparse(base_url).hostname or ""
        for name, value in (cookies or {}).items():
            self.session.cookies.set(name, value, domain=host, path="/")
        self._login_form: Optional[Dict[str, str]] = None
        self._login_action: Optional[str] = None
        # The coordinator's keep-alive and data fetch can overlap in the
        # executor; requests.Session is not safe for concurrent use.
        self._lock = threading.Lock()

    def get_cookies(self) -> Dict[str, str]:
        """Return the session cookies, for persisting across restarts."""
        return self.session.cookies.get_dict()

    def close(self) -> None:
        """Close the underlying HTTP session (does not log out)."""
        self.session.close()

    # ----- login (interactive, from the config flow) -----

    def start_login(self) -> Optional[CaptchaChallenge]:
        """Load the login page in a fresh session and remember its form.

        Returns the captcha to solve, or None when the page's captcha is
        hidden (USMS currently only shows it after the first submit).
        """
        with self._lock:
            response = self._get(self.login_url)
            return self._load_login_form(BeautifulSoup(response.text, "html.parser"), response.url)

    def submit_login(
        self, ic_number: str, password: str, captcha: str = ""
    ) -> Optional[CaptchaChallenge]:
        """Post the login form.

        Returns None once the session is authenticated. USMS logs in in two
        stages: the first submit (captcha hidden, so left empty) redirects to
        ResLogin?sc=<token>, which shows the form again with a visible
        captcha. In that case the challenge is returned and submit_login must
        be called again with its code.
        """
        with self._lock:
            if self._login_form is None or self._login_action is None:
                raise SiteError("start_login must be called before submit_login")
            data = dict(self._login_form)
            action = self._login_action
            self._login_form = self._login_action = None  # a captcha is single-use
            data.update(
                {
                    "__EVENTTARGET": "",
                    "__EVENTARGUMENT": "",
                    FORM_USERNAME: ic_number,
                    FORM_PASSWORD: password,
                    FORM_CAPTCHA: captcha,
                    FORM_LOGIN_BUTTON: "Login",
                }
            )
            response = self._request("post", action, data=data)
            soup = BeautifulSoup(response.text, "html.parser")

            if soup.find("span", {"id": ELEM_USERNAME_SPAN}):
                _LOGGER.info("USMS login successful")
                return None

            # The captcha error cell is always in the page; it is only shown
            # (no visibility:hidden) when the submitted code was rejected.
            captcha_error = soup.find(id=ELEM_CAPTCHA_ERROR)
            if captcha_error and "visibility:hidden" not in (captcha_error.get("style") or ""):
                raise InvalidCaptcha(captcha_error.get_text(" ", strip=True))

            login_error = soup.find("span", {"id": ELEM_LOGIN_ERROR})
            if login_error and login_error.get_text(strip=True):
                raise InvalidCredentials(login_error.get_text(" ", strip=True))

            if "sc=" in urllib.parse.urlparse(response.url).query:
                _LOGGER.debug("USMS accepted the first login stage; another captcha is required")
                challenge = self._load_login_form(soup, response.url)
                if challenge is None:
                    raise SiteError("USMS asked to log in again without showing a captcha")
                return challenge

            raise SiteError("Login response had neither a logged-in page nor an error")

    def _load_login_form(
        self, soup: BeautifulSoup, page_url: str
    ) -> Optional[CaptchaChallenge]:
        """Remember the login form on this page and fetch its captcha if shown."""
        form = soup.find("form")
        img = soup.find("img", {"id": ELEM_CAPTCHA_IMG})
        if not form or not form.find("input", {"name": FORM_VIEWSTATE}) or not img:
            raise SiteError("Login page did not contain the expected form/captcha")

        # Submit every non-button field as the browser would; this carries
        # hidden ASP.NET and DevExpress state along with the captcha.
        self._login_form = {
            tag["name"]: tag.get("value", "")
            for tag in form.find_all("input")
            if tag.get("name") and tag.get("type") not in ("submit", "button", "image")
        }
        # Browsers also post the resources DevExpress loaded (added by its JS).
        self._login_form.update(_devexpress_resource_fields(soup))
        # The second stage posts back to ResLogin?sc=<token>, so honour the action.
        self._login_action = urllib.parse.urljoin(page_url, form.get("action") or page_url)

        captcha_box = soup.find(id=ELEM_CAPTCHA_BOX)
        if captcha_box and "display:none" in (captcha_box.get("style") or "").replace(" ", ""):
            return None

        img_response = self._get(urllib.parse.urljoin(page_url, img["src"]))
        return CaptchaChallenge(
            image=img_response.content,
            content_type=img_response.headers.get("Content-Type", "image/png"),
        )

    # ----- authenticated reads (from the coordinator) -----

    def keep_alive(self) -> None:
        """Touch the session so USMS does not expire it. Raises SessionExpired."""
        with self._lock:
            self._get_home()

    def fetch_meter_data(self) -> Dict[str, Any]:
        """Fetch and parse meter data. Raises SessionExpired if logged out."""
        with self._lock:
            soup = self._get_home()

            iframe = soup.find("iframe", {"id": ELEM_IFRAME})
            if not iframe or not iframe.get("src"):
                raise SiteError("Meter data iframe not found on USMS home page")
            iframe_url = urllib.parse.urljoin(self.base_url, iframe["src"])
            iframe_response = self._get(iframe_url)

        return _format_meter_data(_parse_meter_cards(iframe_response.text))

    # ----- helpers -----

    def _get_home(self) -> BeautifulSoup:
        """GET MainPage; USMS redirects to SessionExpire when logged out."""
        response = self._get(self.main_url)
        soup = BeautifulSoup(response.text, "html.parser")
        if "SessionExpire" in response.url or not soup.find("span", {"id": ELEM_USERNAME_SPAN}):
            raise SessionExpired("USMS session is no longer logged in")
        return soup

    def _get(self, url: str) -> requests.Response:
        return self._request("get", url)

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        try:
            response = self.session.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
            response.raise_for_status()
        except (requests.ConnectionError, requests.Timeout) as err:
            raise CannotConnect(f"Cannot reach USMS website: {err}") from err
        except requests.HTTPError as err:
            status = err.response.status_code if err.response is not None else "unknown"
            raise SiteError(f"USMS returned HTTP {status}") from err
        except requests.RequestException as err:
            raise CannotConnect(str(err)) from err
        return response


def _devexpress_resource_fields(soup: BeautifulSoup) -> Dict[str, str]:
    """Rebuild the DXScript/DXCss fields DevExpress's JS adds on submit.

    They list the DXR.axd resource ids the page loaded, plus (for CSS) the
    page's other <link> hrefs, in document order without duplicates.
    """

    def resource_ids(url: str) -> list[str]:
        query = urllib.parse.urlparse(url).query
        if not query.startswith("r="):
            return []
        return query[2:].rsplit("-", 1)[0].split(",")

    scripts: list[str] = []
    for tag in soup.find_all("script", src=True):
        if "DXR.axd" in tag["src"]:
            scripts.extend(resource_ids(tag["src"]))

    css: list[str] = []
    for tag in soup.find_all("link", href=True):
        entries = resource_ids(tag["href"]) if "DXR.axd" in tag["href"] else [tag["href"]]
        css.extend(e for e in entries if e not in css)

    return {"DXScript": ",".join(scripts), "DXCss": ",".join(css)}


def _parse_meter_cards(html: str) -> list[Dict[str, str]]:
    """Parse meter cards from the iframe content."""
    soup = BeautifulSoup(html, "html.parser")
    filtered_data = []

    for card in soup.find_all("td", class_="dxcvCard"):
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

        # Extract field values by caption
        for span in card.find_all("span", id=lambda x: x and x.endswith("_Cap")):
            field_name = span.get_text(strip=True).rstrip(":").strip()
            if field_name in FIELD_MAPPINGS:
                cell = span.find_next("td", class_="dxflNestedControlCell")
                if cell:
                    meter_data[field_name] = cell.text.strip()

        if meter_data:
            filtered_data.append(meter_data)

    _LOGGER.info("Filtered %d meter entries", len(filtered_data))
    return filtered_data


def _format_meter_data(meter_data: list[Dict[str, str]]) -> Dict[str, Any]:
    """Format meter data for sensor entities."""
    formatted: Dict[str, Any] = {
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
