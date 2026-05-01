"""
<plugin key="ZonneDimmer" name="ZonneDimmer Integration" author="galadril" version="1.1.0" wikilink="https://www.zonnedimmer.nl" externallink="https://github.com/galadril/Domoticz-ZonneDimmer-Plugin">
    <description>
        <h2>ZonneDimmer Integration Plugin</h2><br/>
        This plugin integrates Domoticz with ZonneDimmer to control your solar inverter dimming functionality.<br/>
        <br/>
        Features:<br/>
        - Enable/Disable dimming with a switch device<br/>
        - Set price threshold for dimming with a dimmer slider<br/>
        - Set curtailment percentage (how much to dim) with a dimmer slider<br/>
        - Monitor live power generation and consumption<br/>
    </description>
    <params>
        <param field="Address" label="ZonneDimmer Email" width="300px" required="true"/>
        <param field="Password" label="ZonneDimmer Password" width="300px" required="true" password="true"/>
        <param field="Mode1" label="Device ID" width="400px" required="true" default=""/>
        <param field="Mode2" label="Update Interval (seconds)" width="100px" required="true" default="60"/>
        <param field="Mode6" label="Debug Level" width="200px">
            <options>
                <option label="None" value="0" default="true" />
                <option label="Basic" value="2"/>
                <option label="Connections" value="4"/>
                <option label="Messages" value="8"/>
                <option label="All" value="-1"/>
            </options>
        </param>
    </params>
</plugin>
"""

import Domoticz
import json
import urllib.parse
import urllib.request
import urllib.error
import re
import http.cookiejar

class BasePlugin:

    # Device unit numbers
    UNIT_DIMMING_SWITCH = 1
    UNIT_PRICE_DIMMER = 2
    UNIT_LIVE_POWER = 3
    UNIT_STATUS_TEXT = 4
    UNIT_CURTAILMENT_DIMMER = 5

    def __init__(self):
        self.email = ""
        self.password = ""
        self.device_id = ""
        self.update_interval = 60
        self.bearer_token = None
        self.session_cookie = None
        self.dimming_enabled = False
        self.dim_price = 0.0
        self.curtailment_perc = 0
        self.heartbeat_counter = 0
        return

    def onStart(self):
        Domoticz.Debug("onStart called")

        # Set debug level
        if Parameters["Mode6"]:
            Domoticz.Debugging(int(Parameters["Mode6"]))

        Domoticz.Log("ZonneDimmer Plugin starting...")

        # Get parameters
        self.email = Parameters["Address"]
        self.password = Parameters["Password"]
        self.device_id = Parameters["Mode1"]
        self.update_interval = int(Parameters.get("Mode2", "60"))

        # Validate parameters
        if not self.email or not self.password:
            Domoticz.Error("Email and Password are required!")
            return

        if not self.device_id:
            Domoticz.Log("Device ID not set. You need to configure it after login.")

        # Create devices if they don't exist
        if self.UNIT_DIMMING_SWITCH not in Devices:
            Domoticz.Device(Name="Dimming Enable", Unit=self.UNIT_DIMMING_SWITCH, TypeName="Switch", Image=9).Create()
            Domoticz.Log("Dimming Switch device created.")

        if self.UNIT_PRICE_DIMMER not in Devices:
            Domoticz.Device(Name="Dim Price Threshold", Unit=self.UNIT_PRICE_DIMMER, Type=244, Subtype=73, Switchtype=7, Image=15).Create()
            Domoticz.Log("Price Dimmer device created.")

        if self.UNIT_LIVE_POWER not in Devices:
            Domoticz.Device(Name="Solar Generation", Unit=self.UNIT_LIVE_POWER, TypeName="Usage", Used=1).Create()
            Domoticz.Log("Live Power device created.")

        if self.UNIT_STATUS_TEXT not in Devices:
            Domoticz.Device(Name="Status", Unit=self.UNIT_STATUS_TEXT, TypeName="Text", Used=1).Create()
            Domoticz.Log("Status Text device created.")

        if self.UNIT_CURTAILMENT_DIMMER not in Devices:
            Domoticz.Device(Name="Curtailment Percentage", Unit=self.UNIT_CURTAILMENT_DIMMER, Type=244, Subtype=73, Switchtype=7, Image=15).Create()
            Domoticz.Log("Curtailment Dimmer device created.")

        # Set heartbeat
        Domoticz.Heartbeat(20)

        # Try to login
        self.login()

        Domoticz.Log("ZonneDimmer Plugin started successfully.")

    def onStop(self):
        Domoticz.Debug("onStop called")
        Domoticz.Log("ZonneDimmer Plugin stopped.")

    def onCommand(self, Unit, Command, Level, Hue):
        Domoticz.Debug(f"onCommand called for Unit {Unit}: Command '{Command}', Level: {Level}")

        if Unit == self.UNIT_DIMMING_SWITCH:
            # Dimming enable/disable switch
            if Command == "On":
                self.dimming_enabled = True
                self.update_dimming_settings(True, self.dim_price, self.curtailment_perc)
                Devices[Unit].Update(nValue=1, sValue="On")
                Domoticz.Log("Dimming enabled")
            elif Command == "Off":
                self.dimming_enabled = False
                self.update_dimming_settings(False, self.dim_price, self.curtailment_perc)
                Devices[Unit].Update(nValue=0, sValue="Off")
                Domoticz.Log("Dimming disabled")

        elif Unit == self.UNIT_PRICE_DIMMER:
            # Price threshold dimmer (0-100 maps to -0.50 to +0.50 EUR/kWh)
            # This gives a range of -50 to +50 cents
            self.dim_price = (Level - 50) / 100.0  # Maps 0-100 to -0.50 to +0.50
            self.update_dimming_settings(self.dimming_enabled, self.dim_price, self.curtailment_perc)
            Devices[Unit].Update(nValue=2, sValue=str(Level))
            Domoticz.Log(f"Dim price threshold set to: {self.dim_price:.3f} EUR/kWh")

        elif Unit == self.UNIT_CURTAILMENT_DIMMER:
            # Curtailment percentage dimmer (0-100 directly maps to 0-100%)
            self.curtailment_perc = Level
            self.update_dimming_settings(self.dimming_enabled, self.dim_price, self.curtailment_perc)
            Devices[Unit].Update(nValue=2, sValue=str(Level))
            Domoticz.Log(f"Curtailment percentage set to: {self.curtailment_perc}%")

    def onHeartbeat(self):
        Domoticz.Debug("onHeartbeat called")

        self.heartbeat_counter += 1
        heartbeat_interval = self.update_interval // 20  # Convert seconds to heartbeat cycles (20s each)

        if self.heartbeat_counter >= heartbeat_interval:
            self.heartbeat_counter = 0

            # Ensure we're logged in
            if not self.bearer_token:
                self.login()

            if self.bearer_token and self.device_id:
                # Update live data
                self.update_live_data()

    def login(self):
        """Login to ZonneDimmer API and get session/bearer token"""
        Domoticz.Debug("Attempting to login to ZonneDimmer API")

        try:
            import http.cookiejar
            import re

            # Step 1: Get login page to obtain CSRF token
            Domoticz.Log("Getting login page...")
            UpdateDevice(self.UNIT_STATUS_TEXT, 0, "Logging in...")

            login_page_url = "https://app.zonnedimmer.nl/login"

            # Create cookie jar to handle cookies
            cookie_jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

            # Get login page
            req = urllib.request.Request(login_page_url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0')
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
            req.add_header('Accept-Language', 'en-GB,en;q=0.9')
            req.add_header('Accept-Encoding', 'gzip, deflate, br')
            req.add_header('Connection', 'keep-alive')
            req.add_header('Upgrade-Insecure-Requests', '1')
            response = opener.open(req, timeout=10)
            html = response.read().decode('utf-8')

            # Extract CSRF token
            match = re.search(r'name="_token"\s+value="([^"]+)"', html)
            if not match:
                Domoticz.Error("Could not find CSRF token on login page")
                UpdateDevice(self.UNIT_STATUS_TEXT, 0, "Login failed: No CSRF token")
                return

            csrf_token = match.group(1)
            Domoticz.Debug(f"CSRF token obtained: {csrf_token[:20]}...")

            # Step 2: Perform login
            Domoticz.Log("Submitting login credentials...")

            form_data = {
                '_token': csrf_token,
                'email': self.email,
                'password': self.password,
                'remember': 'on'
            }

            data = urllib.parse.urlencode(form_data).encode('utf-8')
            req = urllib.request.Request(login_page_url, data=data, method='POST')
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0')
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
            req.add_header('Accept-Language', 'en-GB,en;q=0.9')
            req.add_header('Accept-Encoding', 'gzip, deflate, br')
            req.add_header('Content-Type', 'application/x-www-form-urlencoded')
            req.add_header('Origin', 'https://app.zonnedimmer.nl')
            req.add_header('Connection', 'keep-alive')
            req.add_header('Referer', login_page_url)
            req.add_header('Upgrade-Insecure-Requests', '1')

            response = opener.open(req, timeout=10)

            # Step 3: Store session cookies
            cookie_list = []
            for cookie in cookie_jar:
                cookie_list.append(f"{cookie.name}={cookie.value}")

            if cookie_list:
                self.session_cookie = "; ".join(cookie_list)
                Domoticz.Debug(f"Session cookie stored: {len(self.session_cookie)} characters")

            # Step 4: Try to get bearer token from API
            # After login, we can access API endpoints
            Domoticz.Log("Getting API bearer token...")

            dashboard_url = "https://app.zonnedimmer.nl/dashboard"
            req = urllib.request.Request(dashboard_url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0')
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
            req.add_header('Accept-Language', 'en-GB,en;q=0.9')
            req.add_header('Accept-Encoding', 'gzip, deflate, br')
            req.add_header('Connection', 'keep-alive')
            response = opener.open(req, timeout=10)
            dashboard_html = response.read().decode('utf-8')

            # Try to extract bearer token from JavaScript or meta tags
            # Pattern: Bearer tokens are typically in format: number|string
            token_match = re.search(r'Bearer\s+([\d]+\|[\w]+)', dashboard_html)
            if token_match:
                self.bearer_token = token_match.group(1)
                Domoticz.Log(f"Bearer token obtained: {self.bearer_token[:20]}...")
            else:
                # Bearer token might be set via API call, store session for now
                Domoticz.Log("Bearer token not found in page, using session authentication")

            Domoticz.Log("Login successful!")
            UpdateDevice(self.UNIT_STATUS_TEXT, 0, "Connected")

        except urllib.error.HTTPError as e:
            Domoticz.Error(f"HTTP Error during login: {e.code} - {e.reason}")
            UpdateDevice(self.UNIT_STATUS_TEXT, 0, f"Login failed: {e.code}")
        except Exception as e:
            Domoticz.Error(f"Login failed: {str(e)}")
            import traceback
            Domoticz.Error(traceback.format_exc())
            UpdateDevice(self.UNIT_STATUS_TEXT, 0, f"Login failed: {str(e)}")

    def update_live_data(self):
        """Fetch live power generation data"""
        if not self.bearer_token and not self.session_cookie:
            Domoticz.Debug("Cannot update live data: Not logged in")
            return

        Domoticz.Debug("Updating live data from ZonneDimmer API")

        try:
            url = f"https://app.zonnedimmer.nl/api/v1/graphs/live/zonnedimmers/{self.device_id}"

            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0')
            req.add_header('Accept', 'application/json')
            req.add_header('Accept-Language', 'en-GB,en;q=0.9')
            req.add_header('Accept-Encoding', 'gzip, deflate, br')
            req.add_header('X-Requested-With', 'XMLHttpRequest')
            req.add_header('Connection', 'keep-alive')
            req.add_header('Referer', 'https://app.zonnedimmer.nl/dashboard')

            # Use bearer token if available, otherwise use session cookie
            if self.bearer_token:
                req.add_header('Authorization', f'Bearer {self.bearer_token}')

            if self.session_cookie:
                req.add_header('Cookie', self.session_cookie)

            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))

                # Extract live power value
                if 'live' in data and len(data['live']) > 0:
                    power = float(data['live'][0]) * 1000  # Convert kW to W
                    UpdateDevice(self.UNIT_LIVE_POWER, 0, str(power))
                    Domoticz.Debug(f"Solar generation: {power}W")
                    UpdateDevice(self.UNIT_STATUS_TEXT, 0, f"OK - {power}W")

        except urllib.error.HTTPError as e:
            Domoticz.Error(f"HTTP Error updating live data: {e.code} - {e.reason}")
            if e.code == 401:
                self.bearer_token = None
                self.session_cookie = None
                self.login()
        except Exception as e:
            Domoticz.Error(f"Error updating live data: {str(e)}")

    def update_dimming_settings(self, enabled, price, curtailment_perc=0):
        """Update dimming settings on ZonneDimmer

        Based on the actual API call:
        POST to /dashboard/settings with form data:
        - _token: CSRF token (needed for web forms)
        - dynamic_contract: 0 or 1 (for enabling dynamic pricing)
        - min_negative_price_cts: price threshold in cents (e.g., -5 for -0.05 EUR)
        - curtailment_min_perc: curtailment percentage (0-100)
        """
        if not self.bearer_token:
            Domoticz.Log("Cannot update settings: Not logged in")
            return

        Domoticz.Debug(f"Updating dimming settings: enabled={enabled}, price={price}, curtailment={curtailment_perc}%")

        try:
            # Convert price from EUR to cents (e.g., -0.05 EUR -> -5 cents)
            price_cents = int(price * 100)

            # Note: This is a web form POST, not an API call
            # We need to get CSRF token first, which requires session handling
            # For now, we'll try using the session-based approach

            url = "https://app.zonnedimmer.nl/dashboard/settings"

            # We need to get the CSRF token from the settings page first
            # This is a limitation of form-based submissions
            csrf_token = self.get_csrf_token()

            if not csrf_token:
                Domoticz.Error("Could not obtain CSRF token for settings update")
                return

            # Prepare form data
            # dynamic_contract: 1 = enabled, 0 = disabled
            form_data = {
                '_token': csrf_token,
                'dynamic_contract': '1' if enabled else '0',
                'min_negative_price_cts': str(price_cents),
                'curtailment_min_perc': str(curtailment_perc) if curtailment_perc > 0 else ''
            }

            data = urllib.parse.urlencode(form_data).encode('utf-8')

            req = urllib.request.Request(url, data=data, method='POST')
            req.add_header('Content-Type', 'application/x-www-form-urlencoded')
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
            req.add_header('Referer', 'https://app.zonnedimmer.nl/dashboard/settings')
            req.add_header('Origin', 'https://app.zonnedimmer.nl')

            # Use cookie-based authentication (session)
            if hasattr(self, 'session_cookie') and self.session_cookie:
                req.add_header('Cookie', self.session_cookie)

            with urllib.request.urlopen(req, timeout=10) as response:
                Domoticz.Log(f"Settings updated successfully")
                Domoticz.Log(f"  Dynamic contract: {'Enabled' if enabled else 'Disabled'}")
                Domoticz.Log(f"  Price threshold: {price:.3f} EUR/kWh ({price_cents} cents)")
                if curtailment_perc > 0:
                    Domoticz.Log(f"  Curtailment: {curtailment_perc}%")
                UpdateDevice(self.UNIT_STATUS_TEXT, 0, "Settings updated")

        except urllib.error.HTTPError as e:
            Domoticz.Error(f"HTTP Error updating settings: {e.code} - {e.reason}")
            if e.code == 401 or e.code == 419:  # 419 = CSRF token mismatch
                Domoticz.Error("Authentication or CSRF token error. May need to re-login.")
                self.bearer_token = None
                self.login()
        except Exception as e:
            Domoticz.Error(f"Error updating settings: {str(e)}")

    def get_csrf_token(self):
        """Get CSRF token from settings page"""
        try:
            url = "https://app.zonnedimmer.nl/dashboard/settings"

            req = urllib.request.Request(url)
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')

            if hasattr(self, 'session_cookie') and self.session_cookie:
                req.add_header('Cookie', self.session_cookie)

            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode('utf-8')

                # Extract CSRF token from HTML
                # Look for: <input type="hidden" name="_token" value="...">
                import re
                match = re.search(r'name="_token"\s+value="([^"]+)"', html)
                if match:
                    token = match.group(1)
                    Domoticz.Debug(f"CSRF token obtained: {token[:20]}...")
                    return token

                Domoticz.Error("Could not find CSRF token in settings page")
                return None

        except Exception as e:
            Domoticz.Error(f"Error getting CSRF token: {str(e)}")
            return None

def UpdateDevice(Unit, nValue, sValue, TimedOut=0, AlwaysUpdate=False):
    """Helper function to update device"""
    if Unit not in Devices:
        return

    if Devices[Unit].nValue != nValue or Devices[Unit].sValue != sValue or Devices[Unit].TimedOut != TimedOut or AlwaysUpdate:
        Devices[Unit].Update(nValue=nValue, sValue=str(sValue), TimedOut=TimedOut)

global _plugin
_plugin = BasePlugin()

def onStart():
    global _plugin
    _plugin.onStart()

def onStop():
    global _plugin
    _plugin.onStop()

def onCommand(Unit, Command, Level, Hue):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Hue)

def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()
