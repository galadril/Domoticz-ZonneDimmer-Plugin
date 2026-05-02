# 🛠️ Domoticz ZonneDimmer Plugin

This plugin integrates Domoticz with [ZonneDimmer](https://www.zonnedimmer.nl) to control your solar inverter's dimming functionality based on electricity prices.

---

## ✨ Features

- **Enable/Disable Dimming**: Control dimming with a simple switch device in Domoticz
- **Price Threshold Control**: Set the electricity price threshold for when dimming should activate using a slider (dimmer) device
- **Live Monitoring**: Monitor your solar power generation in real-time
- **Status Monitoring**: Get status updates about the connection and current state
- **Automatic Updates**: Configurable update interval for fetching live data

---

## 📥 Installation

1. **Clone or download this repository**:
   ```bash
   cd /path/to/domoticz/plugins
   git clone https://github.com/your-username/Domoticz-ZonneDimmer-Plugin.git ZonneDimmer
   ```

2. **Make the plugin executable**:
   ```bash
   chmod +x /path/to/domoticz/plugins/ZonneDimmer/plugin.py
   ```

3. **Restart Domoticz** to load the plugin:
   ```bash
   sudo service domoticz.sh restart
   ```

---

## ⚙️ Configuration

After installation, add the plugin in Domoticz:

1. Go to **Setup** → **Hardware**
2. Add new hardware with type **ZonneDimmer Integration**
3. Configure the following parameters:

   | Parameter | Description | Required |
   |-----------|-------------|----------|
   | **Email** | Your ZonneDimmer account email | Yes |
   | **Password** | Your ZonneDimmer account password | Yes |
   | **Device ID** | Your ZonneDimmer device ID (UUID format) | Yes |
   | **Update Interval** | How often to fetch live data (in seconds) | Yes (default: 60) |
   | **Debug Level** | Logging verbosity level | No |

4. Click **Add**

---

## 🔍 Finding Your Device ID

To find your ZonneDimmer device ID:

1. Log in to [app.zonnedimmer.nl](https://app.zonnedimmer.nl)
2. Open your browser's Developer Tools (F12)
3. Go to the Network tab
4. Refresh the page
5. Look for API calls to URLs like:
   ```
   https://app.zonnedimmer.nl/api/v1/graphs/live/zonnedimmers/[YOUR-DEVICE-ID]
   ```
6. Copy the UUID (format: `019de428-2b96-71bb-9be0-879ae5dd6269`)

---

## 📊 Devices Created

The plugin creates the following devices in Domoticz:

1. **Dimming Enable** (Switch)
   - Turn dimming on or off
   - When ON, the inverter can be dimmed based on price threshold

2. **Dim Price Threshold** (Setpoint)
   - Set the electricity price threshold for dimming, directly in EUR/kWh
   - Example: set to `-0.05` to dim when price drops below -5 cents/kWh
   - Range: -0.50 to +0.50 EUR/kWh

3. **Solar Generation** (Usage sensor)
   - Shows current solar power generation in Watts
   - Updates automatically based on configured interval

4. **Status** (Text sensor)
   - Shows connection status and current state
   - Displays error messages if connection fails

5. **Curtailment Percentage** (Dimmer/Slider)
   - Set the minimum curtailment/dimming percentage
   - Scale: 0-100 directly maps to 0-100%
   - Position 0 = No minimum curtailment
   - Position 100 = Maximum curtailment (100%)
   - Controls how much the inverter should be dimmed when conditions are met

---

## 🎯 Usage

### Basic Usage

1. **Enable Dimming**:
   - Switch the "Dimming Enable" device to ON

2. **Set Price Threshold**:
   - Adjust the "Dim Price Threshold" slider
   - Example: Set to 40 (= -0.10 EUR/kWh) to dim when prices drop below -10 cents per kWh

3. **Set Curtailment Percentage** (Optional):
   - Adjust the "Curtailment Percentage" slider
   - Example: Set to 50 to dim the inverter by at least 50% when dimming is active
   - Leave at 0 for automatic/default curtailment

4. **Monitor**:
   - Check "Solar Generation" for current power output
   - Check "Status" for connection state

### Price Threshold Examples

| Setpoint Value | Description |
|---------------|-------------|
| -0.50 | Dim only when prices are extremely negative |
| -0.25 | Dim when prices drop below -25 cents/kWh |
| -0.10 | Dim when prices drop below -10 cents/kWh |
|  0.00 | Dim when prices become zero or negative |
| +0.10 | Dim when prices are below 10 cents/kWh |

### Curtailment Percentage Examples

| Slider Position | Curtailment | Description |
|----------------|-------------|-------------|
| 0 | None | No minimum curtailment (automatic) |
| 25 | 25% | Reduce output to 75% of maximum |
| 50 | 50% | Reduce output to 50% of maximum |
| 75 | 75% | Reduce output to 25% of maximum |
| 100 | 100% | Turn off inverter completely |

**Note:** The curtailment percentage sets the *minimum* reduction when dimming is active. ZonneDimmer may apply more curtailment based on market conditions.

---

## 🔧 API Endpoints Used

The plugin uses the following ZonneDimmer API endpoints:

- `POST /login` - Authentication with CSRF token
- `GET /api/v1/graphs/live/zonnedimmers/{id}` - Live power data
- `GET /api/v1/graphs/prices/zonnedimmers/{id}` - Price data  
- `POST /dashboard/settings` - Update dimming settings (form-based)

**Settings Form Parameters:**
- `_token` - CSRF token (obtained from settings page)
- `dynamic_contract` - Enable/disable dynamic pricing (0 or 1)
- `min_negative_price_cts` - Price threshold in cents (e.g., -5 for -0.05 EUR/kWh)
- `curtailment_min_perc` - Minimum curtailment percentage (0-100)

**Note**: The settings endpoint uses web form submission with CSRF token protection, not a REST API. The plugin handles this automatically by:
1. Logging in and storing session cookies
2. Fetching the settings page to get CSRF token
3. Submitting the form with proper authentication

---

## 🐛 Troubleshooting

### Plugin doesn't show up in hardware list
- Make sure plugin.py is executable: `chmod +x plugin.py`
- Restart Domoticz completely
- Check Domoticz logs for Python errors

### "Login failed" or authentication errors
- Verify your email and password are correct
- Check if you can log in to app.zonnedimmer.nl
- The ZonneDimmer API may require CSRF token handling for web login

### "No device ID" warning
- Make sure you've entered your Device ID in the hardware configuration
- Follow the steps in "Finding Your Device ID" section above

### No data updates
- Check your Update Interval setting
- Verify your Device ID is correct
- Check Domoticz logs for error messages
- Enable Debug logging to see detailed API communication

### Settings changes not applying
- The settings API endpoint may need to be confirmed
- Check debug logs for HTTP error codes
- Verify your bearer token is valid

---

## 📝 Important Notes

### Authentication
The ZonneDimmer API uses Bearer token authentication. The current implementation attempts to use API endpoints directly. If web-based login flow is required (with CSRF tokens), you may need to:

1. Manually obtain a bearer token from your browser's developer tools
2. Configure the plugin to use that token
3. Or implement full web login flow with session/CSRF handling

### API Endpoint Confirmation
Some API endpoints (especially the settings update endpoint) may need to be confirmed against the actual ZonneDimmer API. If settings updates don't work:

1. Enable debug logging
2. Check the actual API endpoints in your browser's network tab
3. Update the plugin.py accordingly

---

## 🤝 Contributing

Contributions are welcome! If you find issues or have improvements:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

---

## 📄 License

This plugin is provided as-is for use with Domoticz and ZonneDimmer integration.

---

## 🙏 Credits

- [Domoticz](https://www.domoticz.com) - Home automation system
- [ZonneDimmer](https://www.zonnedimmer.nl) - Solar inverter dimming solution

---

## 📮 Support

For issues and questions:
- Check the [Issues](https://github.com/your-username/Domoticz-ZonneDimmer-Plugin/issues) page
- Review Domoticz logs with debug enabled
- Consult ZonneDimmer documentation

