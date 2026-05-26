# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a single-file Domoticz plugin (`plugin.py`) that integrates the [ZonneDimmer](https://www.zonnedimmer.nl) solar inverter dimming service with the [Domoticz](https://www.domoticz.com) home automation system.

## Validation

Run syntax check locally:
```bash
python -m compileall .
```

Run plugin structure validation:
```bash
python .github/scripts/validate_plugin.py
```

The CI pipeline (`.github/workflows/validate.yml`) runs both on push/PR to `main`.

## Architecture

The entire plugin lives in `plugin.py`. Domoticz loads it as a Python plugin and calls module-level callback functions which delegate to a singleton `BasePlugin` instance.

**Plugin lifecycle callbacks** (module-level, delegating to `_plugin`):
- `onStart()` — reads `Parameters`, creates Domoticz devices if missing, sets heartbeat to 20s, triggers `login()`
- `onStop()` — cleanup
- `onCommand(Unit, Command, Level, Hue)` — handles device commands, calls `update_dimming_settings()`
- `onHeartbeat()` — called every 20s; accumulates cycles until `update_interval` is reached, then calls `update_live_data()`

**Domoticz-specific globals** available at runtime (injected by Domoticz, not imported):
- `Domoticz` — logging and device creation API
- `Parameters` — dict with plugin config (`Address`, `Password`, `Mode1`–`Mode6`)
- `Devices` — dict of unit number → device object

**Device unit numbers** (constants on `BasePlugin`):
| Constant | Unit | Type | Purpose |
|---|---|---|---|
| `UNIT_DIMMING_SWITCH` | 1 | Switch | Enable/disable dimming |
| `UNIT_PRICE_DIMMER` | 2 | Dimmer (0-100) | Price threshold: maps 0-100 → -0.50 to +0.50 EUR/kWh |
| `UNIT_LIVE_POWER` | 3 | Usage sensor | Solar generation in Watts |
| `UNIT_STATUS_TEXT` | 4 | Text | Connection status |
| `UNIT_CURTAILMENT_DIMMER` | 5 | Dimmer (0-100) | Curtailment percentage 0-100% |

**Authentication flow** (all in `login()`):
1. GET `https://app.zonnedimmer.nl/login` — extract CSRF token from HTML
2. POST credentials + CSRF token — session cookies are stored in `self.opener` (cookie jar) and `self.session_cookie`
3. GET `/dashboard` — attempt to extract bearer token via regex from HTML (looks for `localStorage.setItem("token", ...)` or `number|string` pattern)
4. Fallback: GET `/api/user` — check JSON response for `token` or `access_token` field
5. If no bearer token found, session cookies alone are used

**Settings update flow** (`update_dimming_settings()`):
1. GET `/dashboard/settings` via `get_csrf_token()` to extract fresh CSRF token
2. POST form data to `/dashboard/settings` with `_token`, `dynamic_contract`, `min_negative_price_cts` (in cents), `curtailment_min_perc`

**Price conversion**: slider level `L` → EUR/kWh = `(L - 50) / 100`. EUR/kWh → cents = `int(price * 100)`.

**Heartbeat math**: `Domoticz.Heartbeat(20)` means `onHeartbeat` fires every 20 seconds. The counter threshold is `update_interval // 20`.

## Plugin XML Header

The XML `<plugin>` declaration at the top of `plugin.py` (inside a docstring) is required by Domoticz to discover the plugin. The `validate_plugin.py` script parses and validates this header. Required attributes: `key`, `name`, `author`, `version`. Required child elements: `<description>`, `<params>`.
