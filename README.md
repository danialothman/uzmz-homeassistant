# UZMZ Home Assistant Integration

A Home Assistant custom component for monitoring utility meters (electric and water) from USMS (Utility Smart Meter System) in Brunei.

## Features

- 🔌 **Electric Meter Monitoring**: Track remaining units (kWh), balance, status, and last update time
- 💧 **Water Meter Monitoring**: Track remaining units (m³), balance, status, and last update time
- 📊 **Historical Data**: All sensor data stored in Home Assistant database for trending
- 🔄 **Configurable Updates**: Set custom polling intervals (default: 1 hour)
- 🎨 **Beautiful Dashboard**: Pre-configured Lovelace dashboard with gauges, graphs, and statistics
- 🔔 **Alert Support**: Create automations for low balance notifications
- ⚙️ **UI Configuration**: Easy setup through Home Assistant UI

## Installation

### Method 1: Manual Installation

1. Copy the `custom_components/uzmz` directory to your Home Assistant `custom_components` directory:
   ```bash
   cp -r custom_components/uzmz /config/custom_components/
   ```

2. Restart Home Assistant

3. Go to **Settings** → **Devices & Services** → **Add Integration** → Search for "UZMZ"

4. Follow the configuration steps

### Method 2: HACS Installation (Coming Soon)

This integration will be submitted to HACS for easier installation.

## Configuration

### Through UI (Recommended)

1. Navigate to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for "UZMZ Utility Meter Monitor"
4. Enter your credentials:
   - **IC Number**: Your IC number for USMS login
   - **Password**: Your USMS password
   - **Base URL**: (Optional) Default is `https://www.usms.com.bn/SmartMeter/`
5. Type the captcha shown in the next step. USMS asks for a captcha at every login, so the integration signs in once and then keeps that session alive.

### When USMS asks you to sign in again

If the USMS session ends (site restart, long outage, Home Assistant offline for a while), the integration shows **Re-configuration required** under **Settings** → **Devices & Services**. Click **Reconfigure**, submit (your saved password is reused unless you enter a new one), then type the captcha.

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

### Through configuration.yaml (Alternative)

```yaml
# Not recommended - Use UI configuration instead
# This method may be deprecated in future versions
```

## Setting Up the Dashboard

1. Copy the contents of `lovelace-dashboard.yaml`

2. In Home Assistant, go to **Settings** → **Dashboards**

3. Click **Add Dashboard** and create a new dashboard called "Utility Meters"

4. Open the dashboard and click **Edit Dashboard** (three dots menu)

5. Click **Raw Configuration Editor**

6. Paste the dashboard configuration

7. Save and enjoy your new utility meter dashboard!

## Sensors

The integration creates the following sensors:

### Electric Meter
- `sensor.uzmz_electric_remaining_units` - Remaining electricity units (kWh)
- `sensor.uzmz_electric_remaining_balance` - Remaining balance (BND)
- `sensor.uzmz_electric_status` - Meter status
- `sensor.uzmz_electric_last_updated` - Last update timestamp

### Water Meter
- `sensor.uzmz_water_remaining_units` - Remaining water units (m³)
- `sensor.uzmz_water_remaining_balance` - Remaining balance (BND)
- `sensor.uzmz_water_status` - Meter status
- `sensor.uzmz_water_last_updated` - Last update timestamp

## Automation Examples

### Low Balance Alert

```yaml
automation:
  - alias: "Low Electric Balance Alert"
    trigger:
      platform: numeric_state
      entity_id: sensor.uzmz_electric_remaining_balance
      below: 20
    action:
      service: notify.mobile_app_your_phone
      data:
        title: "⚡ Low Electric Balance"
        message: "Your electric meter balance is {{ states('sensor.uzmz_electric_remaining_balance') }} BND"
        
  - alias: "Low Water Balance Alert"
    trigger:
      platform: numeric_state
      entity_id: sensor.uzmz_water_remaining_balance
      below: 20
    action:
      service: notify.mobile_app_your_phone
      data:
        title: "💧 Low Water Balance"
        message: "Your water meter balance is {{ states('sensor.uzmz_water_remaining_balance') }} BND"
```

### Daily Usage Report

```yaml
automation:
  - alias: "Daily Utility Report"
    trigger:
      platform: time
      at: "20:00:00"
    action:
      service: notify.mobile_app_your_phone
      data:
        title: "Daily Utility Report"
        message: |
          Electric: {{ states('sensor.uzmz_electric_remaining_units') }} kWh ({{ states('sensor.uzmz_electric_remaining_balance') }} BND)
          Water: {{ states('sensor.uzmz_water_remaining_units') }} m³ ({{ states('sensor.uzmz_water_remaining_balance') }} BND)
```

## Energy Dashboard Integration

To add electric meter to Home Assistant's Energy Dashboard:

1. Go to **Settings** → **Dashboards** → **Energy**
2. Click **Add Consumption**
3. Select `sensor.uzmz_electric_remaining_units`
4. Configure as needed

## Options

You can configure the update interval in the integration options:

1. Go to **Settings** → **Devices & Services**
2. Find the UZMZ integration
3. Click **Configure**
4. Set the update interval (300-86400 seconds)

## Troubleshooting

### Authentication Failed
- Verify your IC number and password
- Check if you can login to the USMS website directly
- Ensure your account is active

### No Data Available
- Check your internet connection
- Verify the USMS website is accessible
- Check Home Assistant logs for errors

## Debug Logging

Add this to your `configuration.yaml` to enable debug logging:

```yaml
logger:
  default: info
  logs:
    custom_components.uzmz: debug
```

## Support

For issues, feature requests, or questions:
- Check the [Home Assistant logs](https://www.home-assistant.io/docs/configuration/troubleshooting/#enabling-debug-logging)
- Open an issue on [GitHub](https://github.com/danial/uzmz-homeassistant)

## Contributing

Contributions are welcome! Please feel free to submit pull requests.

## License

This project is licensed under the MIT License.

## Disclaimer

This integration is not affiliated with or endorsed by USMS or any utility provider. Use at your own risk.

## Credits

Based on the original UZMZ Python script for utility meter monitoring.