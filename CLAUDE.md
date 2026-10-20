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
  coordinator.py    - Data update coordinator (session reuse, keep-alive, reauth, live usage)
  usage.py          - Anchors the login-time readings to the live usage reports
  sensor.py         - Sensor entity definitions (8 meter sensors: 4 electric, 4 water; plus last/next refresh and the USMS platform version)
  manifest.json     - Integration metadata
  strings.json      - UI strings
  translations/en.json - English translations
lovelace-dashboard.yaml - Utility dashboard view (needs examples/uzmz_package.yaml)
examples/uzmz_package.yaml - Optional helper sensors (consumption, cost, days left, sign-in)
hacs.json            - HACS metadata
LICENSE              - MIT
custom_components/uzmz/brand/ - Icons (HACS reads these; original design, no USMS branding)
tests/               - pytest suite for api.py (no HA needed)
.github/workflows/validate.yml - hassfest, HACS and tests
```

## Architecture

- **Pattern**: HA Coordinator pattern (ConfigFlow -> Coordinator -> CoordinatorEntity)
- **Data source**: Web scraping USMS website using `requests` + `BeautifulSoup4`
- **Auth**: ASP.NET form-based auth (__VIEWSTATE, __EVENTVALIDATION) in two stages: IC + password posts to `ResLogin` and redirects to `ResLogin?sc=<token>`, which shows a DevExpress captcha; IC + password + captcha there logs in (lands on `MainPage`). Since about 2026-10-12 USMS often logs in straight after stage 1 with no captcha (the flow's `LOGGED_IN` path). Login happens in the config/reauth flow, or as an automatic sign-in (below); the session cookies are stored in the entry (the password only if the user opts in via `save_password`, default off; reauth then asks for it) and the coordinator only GETs `MainPage` and the `Default.aspx` data iframe, with a keep-alive every 5 minutes. The Home cards only show the readings as of login for the whole session, so `usage.py` anchors that snapshot to the live Usage History reports (Daily for past days, which only accepts dates before today; Hourly for today, electric only) and computes current units and an estimated balance (electric: the DES tiered residential tariff in `usage.ELECTRIC_TARIFF`, by month-to-date usage; water: the USMS-listed domestic tariff in `usage.WATER_TARIFF`, B$0.11/m³ for the first 54.54 m³ a month then B$0.44; a learned rate is the fallback for meters without a tariff); the anchors and learned rates live in the entry data. When a report fails mid-session, the previous poll's estimate is kept (`usage.last_estimate`), never the older login snapshot, so remaining units don't jump back up and get counted twice by the consumption helpers. The options flow applies a new scan interval to the coordinator directly: there's no update listener, because HA (from 2026.12) requires an entry with one to reload through it, and reauth/reconfigure already reload via `async_update_reload_and_abort`. The coordinator also keeps each meter's last few days of report totals (`usage_seen`) and when they last changed (`usage_changed`, via `usage.usage_changed`), exposed as the "Usage Updated" timestamp sensors so users can alert when USMS stops posting readings. Until a change is seen, that time is estimated from the reports (`usage.last_posted`: now if today has usage, else the end of the latest day with usage), so a meter USMS has stopped posting for isn't stuck on unknown. Just after midnight the Hourly report says "consumption history not found" (`api.NoUsageData`); for today's hourly report that counts as 0, not a failure. Report dates must be posted as DevExpress raw values (`<field>$State`), not just the text box. Never GET the base URL or `ResLogin` with a live session (USMS ends the session). The only unattended login is automatic sign-in (v1.7.0, since USMS stopped showing the captcha to most logins in October 2026): only with `save_password` on, `AUTO_SIGN_IN_DELAY` (60 minutes) after the session ends so a website/app session isn't cut off, one attempt per ended session (`USMSClient.sign_in`, which stops without posting the captcha stage if one is shown). A rejected password sets `auto_sign_in_blocked` in the entry until a manual sign-in clears it. The session end time and the attempt are stored in the entry (`session_ended`, `auto_sign_in_tried`), so a restart or setup retry during the wait keeps the same due time and still tries only once. On success the coordinator swaps in the new client and carries on in the same update; there's no reload. It's announced by the `uzmz_auto_sign_in` notification, which the example package's `binary_sensor.uzmz_signing_in_again` and the dashboard's "Signing in again soon" banner follow. Never add other unattended login POSTs or retries (account locks after 5 failures). `scripts/live_test.py` exercises `api.py` against the live site using `.env` credentials.
- **IoT class**: cloud_polling
- **Domain**: `uzmz`

## Key Technical Details

- Sensors: remaining units (kWh/m3), balance (BND), status, last updated — for both electric and water
- Timestamps use Asia/Brunei timezone
- USMS platform version (`lblVer` span on `MainPage`, only visible when logged in): 6.1.1001 as of 2026-10-09, when the two-stage captcha login and shifted meter-card layout were observed. `api.py` reads it on every `MainPage` GET; it's exposed as `sensor.uzmz_integration_usms_platform_version`, and the coordinator creates a one-time `uzmz_platform_changed` notification when it differs from the last version seen (stored as `platform_version` in the entry; defaults to `const.KNOWN_PLATFORM_VERSION`). Update `KNOWN_PLATFORM_VERSION` after verifying a new version.
- Scan interval configurable via options flow (300-86400 seconds, default 1 hour)
- Unique ID per config entry is the IC number
- Scope (v1.7.2): one USMS account per HA and one electric + one water meter per account, the only setup that's been tested. Sensor unique IDs and device identifiers aren't per entry (e.g. `uzmz_electric_remaining_unit`), so the config flow aborts a second account (`single_account`) instead of letting its sensors silently clash. Extra meters of a type are ignored (the first card is kept); `USMSClient.meter_counts` counts them and the coordinator shows a once-per-start `uzmz_unsupported_meters` notification. Multi-account support would need per-entry unique IDs with an entity-registry migration that keeps the existing entity IDs. Multi-meter needs a real (redacted) page from such an account first.
- Every request sends `const.USER_AGENT` (`UZMZ/<VERSION> (Home Assistant integration; +<repo URL>)`, v1.7.3), so USMS can see what the traffic is and who to contact. `const.VERSION` must match `manifest.json` (a test checks); bump both on release. Verified 2026-10-20: USMS serves the same login page to it as to the old `python-requests` default.
- Runtime dependency: `beautifulsoup4>=4.12.3`
- No explicit HA version constraint, but tested with HA 2026.2

## Development

- Tests: `pytest` (`pip install -r requirements_test.txt`) runs `tests/test_api.py`, which covers the USMS client (login stages, errors, parsing) against synthetic pages, without Home Assistant
- CI (`.github/workflows/validate.yml`): hassfest, HACS validation and the tests on every push and PR, plus weekly
- No build system
- No `pyproject.toml` or `setup.py`
- End-to-end testing is manual against a live HA instance or with `scripts/live_test.py`
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
   - `python scripts/live_test.py usage`: the live Usage History reports (daily + hourly) next to the Home snapshot; check these still parse if readings stop updating.
   - Every response is dumped to `.live_test/<command>_NN_<method>.html` with its redirect chain in the first line. Diagnose from these.
4. **Version**: check `lblVer` on `MainPage` (6.1.1001 as of 2026-10-09), or the user's `sensor.uzmz_integration_usms_platform_version`. A change likely means the site changed.
5. **If the script fails but a browser works**: open the login page in Chrome, install a JS hook that records the form fields on submit (redact the password) into `localStorage`, have the **user** type credentials + captcha (Claude must not enter passwords or solve captchas), re-install the hook after each page load, then diff the browser's POST against the script's. This is how the missing `DXScript`/`DXCss` fields and the `MainPage` landing page were found. Avoid query strings in tool output (blocked).

**Lockout safety** (USMS locks the account after 5 failed logins, and the counter isn't always shown):
- The script refuses logins after a credentials rejection until `.live_test/STOP` is deleted.
- Captcha codes are 5 chars; if a glyph is ambiguous (e.g. `lv` vs `h`), the user should fetch a fresh one rather than guess.
- If a submit fails silently (redirect with no error), stop and investigate before retrying.
- Never GET the base URL or `ResLogin` with a live session; it ends the session (including the user's browser session).
- USMS allows one session per account: a `live_test.py` login (or the user logging in on the website/app) ends Home Assistant's session, which then asks the user to sign in again. Warn the user before a live-test login. (Observed 2026-10-10: test login 11:19, HA sign-in required 11:20.)

**Cleanup**: delete `.live_test/` afterwards; it holds a live session cookie and personal data from the account pages.
