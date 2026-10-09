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
