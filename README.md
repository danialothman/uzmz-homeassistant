# UZMZ Home Assistant Integration

A Home Assistant custom integration for monitoring prepaid electric and water meters on USMS (Utility Smart Meter System) in Brunei.

> **Unofficial.** This is an independent open-source project. It is not affiliated with, endorsed by or supported by USMS or any utility provider. It reads the same pages you see when you log in to the USMS website. Use it at your own risk.

## Features

- 🔌 **Electric meter**: remaining units (kWh), balance (BND), meter status and USMS reading date
- 💧 **Water meter**: remaining units (m³), balance (BND), meter status and USMS reading date
- 🔐 **Captcha-aware login**: you solve the USMS captcha once in the Home Assistant UI, then the integration keeps the session alive
- 🔔 **Sign-in alerts**: a notification when USMS ends the session and you need to sign in again
- 📊 **History**: all sensors are recorded by Home Assistant for graphs and statistics
- 🔄 **Configurable polling**: default every hour

## Installation

### HACS (recommended)

1. In Home Assistant, open **HACS** → ⋮ → **Custom repositories**.
2. Add `https://github.com/danialothman/uzmz-homeassistant` with the type **Integration**.
3. Search for **UZMZ Utility Meter Monitor** in HACS and download it.
4. Restart Home Assistant.

### Manual

1. Copy `custom_components/uzmz` into your Home Assistant `config/custom_components/` folder.
2. Restart Home Assistant.

## Configuration

1. Go to **Settings** → **Devices & Services** → **Add Integration** and search for **UZMZ**.
2. Enter your USMS login:
   - **IC Number** and **Password**: the ones you use on the USMS website
   - **Remember password**: off by default; see [Privacy & security](#privacy--security)
   - **Base URL**: optional, defaults to `https://www.usms.com.bn/SmartMeter/`
3. Type the captcha shown in the next step.

USMS asks for a captcha at every login, so the integration signs in once and then keeps that session alive. It never logs in on its own. USMS locks an account after 5 failed logins, so it only ever submits a login when you do.

### When USMS asks you to sign in again

If the USMS session ends (site restart, long outage, Home Assistant offline for a while), the integration shows **Re-configuration required** under **Settings** → **Devices & Services**. Click **Reconfigure**, enter your password (or leave it blank if you chose **Remember password**), then type the captcha.

It also creates a **UZMZ — Sign-in required** notification (ID `uzmz_reauth_required`), which clears itself once you have signed in again. To get it on your phone, add an automation (replace `notify.mobile_app_your_phone` with your notify service):

```yaml
automation:
  - alias: "UZMZ sign-in required"
    triggers:
      - trigger: persistent_notification
        notification_id: uzmz_reauth_required
        update_type: added
    actions:
      - action: notify.mobile_app_your_phone
        data:
          title: "UZMZ — Sign-in required"
          message: "The USMS session expired. Open Home Assistant and solve the captcha to resume meter readings."
          data:
            url: /config/integrations/integration/uzmz
```

### Options

Go to **Settings** → **Devices & Services** → **UZMZ** → **Configure** to change:

- **Update interval**: how often to fetch meter data (300–86400 seconds, default 3600)
- **Remember password**: turning it off deletes the saved password immediately; turning it on saves your password the next time you sign in

## Privacy & security

UZMZ only talks to the USMS website, over HTTPS with certificate checks. There is no other server, no telemetry and no analytics.

What is stored, in Home Assistant's own configuration storage (`config/.storage/core.config_entries`), like every other integration:

| Item | Stored? | Why |
|---|---|---|
| IC number | Yes | Identifies your USMS account |
| USMS session cookie | Yes | Lets readings continue after a Home Assistant restart without solving a new captcha. While it is valid, it gives the same access as being logged in on the USMS website. |
| Password | **Only if you turn on Remember password** (off by default) | Lets you sign in again with just the captcha |

Things to know:

- Home Assistant stores integration data **unencrypted** on its own disk. Anyone with access to your Home Assistant files, or to an unencrypted Home Assistant backup, can read it. Keep backups private, or turn on backup encryption.
- Your password, IC number and session cookie are never written to the logs or to notifications. On a failed login only USMS's own error message is logged.
- UZMZ never logs in by itself. It only submits a login when you do (setup or reauth), so it cannot lock your account. USMS locks accounts after 5 failed logins.
- Between sign-ins UZMZ only reads two pages: your meter page every polling interval, plus a light keep-alive every 5 minutes so the session doesn't time out.
- Removing the integration deletes everything it stored.

## Sensors

Entity IDs are generated from the device and sensor names. These are the defaults.

| Electric meter | Water meter | |
|---|---|---|
| `sensor.uzmz_electric_meter_electric_remaining_units` | `sensor.uzmz_water_meter_water_remaining_units` | Remaining units (kWh / m³) |
| `sensor.uzmz_electric_meter_electric_remaining_balance` | `sensor.uzmz_water_meter_water_remaining_balance` | Remaining balance (BND) |
| `sensor.uzmz_electric_meter_electric_meter_status` | `sensor.uzmz_water_meter_water_meter_status` | Meter status, e.g. `ACTIVE` |
| `sensor.uzmz_electric_meter_electric_last_updated` | `sensor.uzmz_water_meter_water_last_updated` | Date of the USMS reading |

The integration also has `sensor.uzmz_integration_last_refresh` (last successful sync) and `sensor.uzmz_integration_next_refresh`.

USMS reports what is **left** on a prepaid meter, not what was used. The remaining-units sensors therefore can't be added to the Energy dashboard directly, because they drop as you use power and jump at every top-up.

## Dashboard

`lovelace-dashboard.yaml` is a ready-made dashboard. Create a new dashboard under **Settings** → **Dashboards**, open it, choose **Edit** → ⋮ → **Raw configuration editor**, and paste the file in.

## Automation examples

### Low balance alert

The thresholds below match the USMS SMS alerts (electric 15 BND, water 5 BND).

```yaml
automation:
  - alias: "UZMZ low balance"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.uzmz_electric_meter_electric_remaining_balance
        below: 15
        id: Electric
      - trigger: numeric_state
        entity_id: sensor.uzmz_water_meter_water_remaining_balance
        below: 5
        id: Water
    conditions:
      # Coming back from unavailable (e.g. after a sign-in) isn't a new drop.
      - condition: template
        value_template: "{{ trigger.from_state.state not in ['unavailable', 'unknown'] }}"
    actions:
      - action: notify.mobile_app_your_phone
        data:
          title: "UZMZ — {{ trigger.id }} balance low"
          message: "{{ trigger.id }} balance is {{ trigger.to_state.state }} BND. Time to top up."
```

### Daily report

```yaml
automation:
  - alias: "UZMZ daily report"
    triggers:
      - trigger: time
        at: "20:00:00"
    actions:
      - action: notify.mobile_app_your_phone
        data:
          title: "Daily utility report"
          message: >
            Electric: {{ states('sensor.uzmz_electric_meter_electric_remaining_units') }} kWh
            ({{ states('sensor.uzmz_electric_meter_electric_remaining_balance') }} BND)
            Water: {{ states('sensor.uzmz_water_meter_water_remaining_units') }} m³
            ({{ states('sensor.uzmz_water_meter_water_remaining_balance') }} BND)
```

## Troubleshooting

- **Invalid credentials**: check that you can log in on the USMS website with the same IC number and password. Stop after a couple of failures, because USMS locks the account after 5.
- **Wrong captcha**: a new captcha is shown automatically; type the new one.
- **No data or sensors unavailable**: check the Home Assistant logs and that the USMS website is up. If the session ended, follow [When USMS asks you to sign in again](#when-usms-asks-you-to-sign-in-again).
- **The integration broke after a USMS website change**: please [open an issue](https://github.com/danialothman/uzmz-homeassistant/issues). Developers can use `scripts/live_test.py` to test the client against the live site (see `CLAUDE.md`).

### Debug logging

```yaml
logger:
  default: info
  logs:
    custom_components.uzmz: debug
```

## Contributing

Issues and pull requests are welcome at [github.com/danialothman/uzmz-homeassistant](https://github.com/danialothman/uzmz-homeassistant).

## License

[MIT](LICENSE)

## Credits

Based on the original UZMZ Python script for utility meter monitoring.
