# CLAUDE.md

## Project Overview

UZMZ is a Home Assistant custom integration for monitoring utility meters (electric and water) from USMS (Utility Smart Meter System) in Brunei. It scrapes the USMS website to extract meter data and exposes it as HA sensors.

## Project Structure

```
custom_components/uzmz/
  __init__.py       - Integration entry point, setup/unload
  config_flow.py    - UI configuration flow + options flow
  const.py          - Constants, defaults, sensor definitions
  api.py            - USMS website client (captcha login, session reads, parsing)
  coordinator.py    - Data update coordinator (session reuse, keep-alive, reauth)
  sensor.py         - Sensor entity definitions (8 sensors: 4 electric, 4 water)
  manifest.json     - Integration metadata
  strings.json      - UI strings
  translations/en.json - English translations
lovelace-dashboard.yaml - Pre-configured dashboard
```

## Architecture

- **Pattern**: HA Coordinator pattern (ConfigFlow -> Coordinator -> CoordinatorEntity)
- **Data source**: Web scraping USMS website using `requests` + `BeautifulSoup4`
- **Auth**: ASP.NET form-based auth (__VIEWSTATE, __EVENTVALIDATION) in two stages: IC + password posts to `ResLogin` and redirects to `ResLogin?sc=<token>`, which shows a DevExpress captcha; IC + password + captcha there logs in (lands on `MainPage`). Login only happens interactively in the config/reauth flow; the session cookies are stored in the entry and the coordinator only GETs `MainPage` and the `Default.aspx` data iframe, with a keep-alive every 5 minutes. Never GET the base URL or `ResLogin` with a live session (USMS ends the session), and never add unattended login POSTs (account locks after 5 failures). `scripts/live_test.py` exercises `api.py` against the live site using `.env` credentials.
- **IoT class**: cloud_polling
- **Domain**: `uzmz`

## Key Technical Details

- Sensors: remaining units (kWh/m3), balance (BND), status, last updated — for both electric and water
- Timestamps use Asia/Brunei timezone
- USMS platform version (`lblVer` span on `MainPage`, only visible when logged in): 6.1.1001 as of 2026-10-09, when the two-stage captcha login and shifted meter-card layout were observed
- Scan interval configurable via options flow (300-86400 seconds, default 1 hour)
- Unique ID per config entry is the IC number
- Runtime dependency: `beautifulsoup4>=4.12.3`
- No explicit HA version constraint, but tested with HA 2026.2

## Development

- No test suite, CI/CD, or build system
- No `pyproject.toml` or `setup.py`
- Testing is manual against a live HA instance
- Debug logging: set `custom_components.uzmz: debug` in HA logger config

## Commands

No build/lint/test commands. To install locally:
```bash
cp -r custom_components/uzmz /path/to/ha/config/custom_components/
```

## Live Testing

When USMS changes and the integration breaks, test `api.py` against the live site with `scripts/live_test.py` (no HA needed).

1. **Credentials**: the user copies `.env.example` to `.env` and fills it in themselves (gitignored; never ask for credentials in chat).
2. **Zero-cost check first**: fetch the public login page (no login) and compare against known structure: captcha hidden on the first page (`display:none`), form field names in `const.py`, stage 2 at `ResLogin?sc=`.
3. **Step through a login**:
   - `python scripts/live_test.py captcha`: submits stage 1, saves the stage-2 captcha to `.live_test/captcha.png`.
   - The **user** opens the image and runs `python scripts/live_test.py login <code>` (e.g. via `! python scripts/live_test.py login <code>`): submits stage 2, then fetches and prints parsed meter data. Claude must not solve captchas.
   - `python scripts/live_test.py fetch` / `keepalive`: session reuse from a new process (simulates an HA restart) / keep-alive only.
   - Every response is dumped to `.live_test/<command>_NN_<method>.html` with its redirect chain in the first line. Diagnose from these.
4. **Version**: check `lblVer` on `MainPage` (6.1.1001 as of 2026-10-09). A change likely means the site changed.
5. **If the script fails but a browser works**: open the login page in Chrome, install a JS hook that records the form fields on submit (redact the password) into `localStorage`, have the **user** type credentials + captcha (Claude must not enter passwords or solve captchas), re-install the hook after each page load, then diff the browser's POST against the script's. This is how the missing `DXScript`/`DXCss` fields and the `MainPage` landing page were found. Avoid query strings in tool output (blocked).

**Lockout safety** (USMS locks the account after 5 failed logins, and the counter isn't always shown):
- The script refuses logins after a credentials rejection until `.live_test/STOP` is deleted.
- Captcha codes are 5 chars; if a glyph is ambiguous (e.g. `lv` vs `h`), the user should fetch a fresh one rather than guess.
- If a submit fails silently (redirect with no error), stop and investigate before retrying.
- Never GET the base URL or `ResLogin` with a live session; it ends the session (including the user's browser session).

**Cleanup**: delete `.live_test/` afterwards; it holds a live session cookie and personal data from the account pages.
