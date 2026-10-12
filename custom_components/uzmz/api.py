"""USMS website client for the UZMZ integration.

USMS logs in in two stages: IC number + password, then (on ResLogin?sc=...)
IC number + password + a DevExpress captcha. So this client never logs in
unattended. A login happens only from the config/reauth flow, where the user
solves the captcha; afterwards the coordinator reuses the authenticated
ASP.NET session (cookies) for read-only GETs and keeps it alive.

Never GET the base URL or ResLogin with a live session: USMS ends the session
whenever the login page is served.
"""

import datetime as dt
import logging
import re
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
    ELEM_PLATFORM_VERSION,
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

# Usage History report types: (cboType value, cboType text)
REPORT_DAILY = ("1", "Daily (Max 1 month)")
REPORT_HOURLY = ("3", "Hourly (Max 1 day)")
_EPOCH = dt.datetime(1970, 1, 1)


class USMSError(Exception):
    """Base error for USMS client failures."""


class CannotConnect(USMSError):
    """USMS could not be reached."""


class SiteError(USMSError):
    """USMS returned something unexpected (maintenance, layout change)."""


class NoUsageData(SiteError):
    """A usage report has no data for the dates asked (e.g. just after midnight)."""


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
        # USMS platform version from the last MainPage read (None if not shown).
        self.platform_version: Optional[str] = None
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
        action = urllib.parse.urljoin(page_url, form.get("action") or page_url)
        # Credentials are posted here, so never follow a form to another site.
        if not _same_origin(action, self.base_url):
            raise SiteError("Login form points to a different site; refusing to send credentials")
        self._login_action = action

        captcha_box = soup.find(id=ELEM_CAPTCHA_BOX)
        if captcha_box and "display:none" in (captcha_box.get("style") or "").replace(" ", ""):
            return None

        img_response = self._get(urllib.parse.urljoin(page_url, img["src"]))
        return CaptchaChallenge(
            image=img_response.content,
            content_type=_captcha_content_type(img_response.headers.get("Content-Type")),
        )

    # ----- authenticated reads (from the coordinator) -----

    def keep_alive(self) -> None:
        """Touch the session so USMS does not expire it. Raises SessionExpired."""
        with self._lock:
            self._get_home()

    def fetch_usage(
        self, usage_path: str, start: dt.date, today: dt.date, hourly: bool
    ) -> Dict[str, float]:
        """Return consumption per date (ISO date -> units) from start to today.

        USMS's Home cards only show the readings as of login, but its usage
        reports are live. Past days come from the Daily report (it only
        accepts dates before today); today's total comes from the Hourly
        report, which only electric meters have. Raises SessionExpired.
        """
        usage: Dict[str, float] = {}
        with self._lock:
            yesterday = today - dt.timedelta(days=1)
            if start <= yesterday:
                rows = self._usage_report(usage_path, REPORT_DAILY, start, yesterday)
                for label, value in rows:
                    day = dt.datetime.strptime(label, "%d/%m/%Y").date()
                    usage[day.isoformat()] = value
            if hourly:
                try:
                    rows = self._usage_report(usage_path, REPORT_HOURLY, today, today)
                except NoUsageData:
                    # Nothing posted for today yet (every night after midnight).
                    rows = []
                usage[today.isoformat()] = round(sum(value for _, value in rows), 3)
        return usage

    def _usage_report(
        self, usage_path: str, report: tuple[str, str], start: dt.date, end: dt.date
    ) -> list[tuple[str, float]]:
        """Run a Usage History report for one meter and return its rows."""
        url = urllib.parse.urljoin(self.base_url, usage_path)
        response = self._get(url)
        if "SessionExpire" in response.url or "ResLogin" in response.url:
            raise SessionExpired("USMS session is no longer logged in")
        form = BeautifulSoup(response.text, "html.parser").find("form")
        if not form:
            raise SiteError("Usage report page did not contain a form")
        data = {
            tag["name"]: tag.get("value", "")
            for tag in form.find_all("input")
            if tag.get("name") and tag.get("type") not in ("submit", "button", "image")
        }
        data.update(
            {
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                "cboType_VI": report[0],
                "cboType": report[1],
                "btnRefresh": "Search",
            }
        )
        for field, day in (("cboDateFrom", start), ("cboDateTo", end)):
            data[field] = day.strftime("%d/%m/%Y")
            # The server reads the date edit's raw value (milliseconds, local
            # time written as if it were UTC), not the text box.
            raw = str(int((dt.datetime.combine(day, dt.time()) - _EPOCH).total_seconds() * 1000))
            data[f"{field}$State"] = '{"rawValue":"%s"}' % raw
            data[f"{field}_Raw"] = raw
        response = self._request("post", response.url, data=data)
        return _parse_usage_rows(response.text)

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
        self.platform_version = _parse_platform_version(soup)
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


def _same_origin(url: str, base_url: str) -> bool:
    """True if url has the same scheme, host and port as base_url."""
    a, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(base_url)
    return (a.scheme, a.netloc.lower()) == (b.scheme, b.netloc.lower())


_CAPTCHA_TYPES = ("image/png", "image/jpeg", "image/gif")


def _captcha_content_type(header: Optional[str]) -> str:
    """The captcha's image type, limited to known image types.

    It ends up inside markdown in the config flow (as a data: URI), so a
    header from the site must not be able to inject anything there.
    """
    content_type = (header or "").split(";")[0].strip().lower()
    return content_type if content_type in _CAPTCHA_TYPES else "image/png"


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

        # Each card links to its meter's Usage History report.
        report_link = card.find("a", href=lambda x: x and x.startswith("Report/UsageHistory?p="))
        if report_link and meter_data:
            meter_data["Usage Path"] = report_link["href"]

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
                "usage_path": meter.get("Usage Path"),
            }

    return formatted


def _parse_usage_rows(html: str) -> list[tuple[str, float]]:
    """Parse a Usage History grid into (date or hour, consumption) rows."""
    soup = BeautifulSoup(html, "html.parser")
    error = soup.find("span", {"id": ELEM_LOGIN_ERROR})
    if error and error.get_text(strip=True):
        message = f"USMS usage report: {error.get_text(' ', strip=True)}"
        if "history not found" in message.lower():
            raise NoUsageData(message)
        raise SiteError(message)
    rows = []
    for tr in soup.select("tr.dxgvDataRow"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) >= 2 and cells[0]:
            try:
                rows.append((cells[0], float(cells[1].replace(",", ""))))
            except ValueError:
                _LOGGER.debug("Skipping usage row %s", cells[:2])
    return rows


def _parse_platform_version(soup: BeautifulSoup) -> Optional[str]:
    """Return the version in MainPage's lblVer label (e.g. "6.1.1001"), if shown."""
    label = soup.find(id=re.compile(rf"(^|_){ELEM_PLATFORM_VERSION}$"))
    if not label:
        return None
    match = re.search(r"\d+(?:\.\d+)+", label.get_text(" ", strip=True))
    return match.group(0) if match else None
