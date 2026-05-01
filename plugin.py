"""
<plugin key="ZonneDimmer" name="ZonneDimmer Integration" author="galadril" version="1.2.0" wikilink="https://www.zonnedimmer.nl" externallink="https://github.com/galadril/Domoticz-ZonneDimmer-Plugin">
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
import gzip

def decompress_response(response_data):
    """Decompress response data if it's gzip compressed"""
    # Check if gzip compressed (magic number 0x1f 0x8b)
    if len(response_data) >= 2 and response_data[:2] == b'\x1f\x8b':
        return gzip.decompress(response_data).decode('utf-8')
    else:
        return response_data.decode('utf-8')

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
        self.opener = None
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

        # Log configuration
        Domoticz.Log(f"Configuration:")
        Domoticz.Log(f"  Email: {self.email}")
        Domoticz.Log(f"  Device ID: {self.device_id if self.device_id else 'NOT SET'}")
        Domoticz.Log(f"  Update interval: {self.update_interval} seconds")

        # Validate parameters
        if not self.email or not self.password:
            Domoticz.Error("Email and Password are required!")
            return

        if not self.device_id:
            Domoticz.Error("Device ID not set! Plugin will not fetch data.")
            Domoticz.Error("Please set Device ID in hardware configuration: 019de428-2b96-71bb-9be0-879ae5dd6269")

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
            if not self.bearer_token and not self.session_cookie:
                self.login()

            if (self.bearer_token or self.session_cookie) and self.device_id:
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
            self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

            # Get login page
            req = urllib.request.Request(login_page_url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0')
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
            req.add_header('Accept-Language', 'en-GB,en;q=0.9')
            req.add_header('Accept-Encoding', 'gzip, deflate')
            req.add_header('Connection', 'keep-alive')
            req.add_header('Upgrade-Insecure-Requests', '1')
            response = self.opener.open(req, timeout=10)
            Domoticz.Log(f"Login page loaded: HTTP {response.status}")

            # Read and decompress response
            html = decompress_response(response.read())
            Domoticz.Debug(f"Login page HTML size: {len(html)} bytes")

            # Extract CSRF token
            match = re.search(r'name="_token"\s+value="([^"]+)"', html)
            if not match:
                Domoticz.Error("Could not find CSRF token on login page")
                UpdateDevice(self.UNIT_STATUS_TEXT, 0, "Login failed: No CSRF token")
                return

            csrf_token = match.group(1)
            Domoticz.Log(f"CSRF token obtained: {csrf_token[:20]}...")

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
            req.add_header('Accept-Encoding', 'gzip, deflate')
            req.add_header('Content-Type', 'application/x-www-form-urlencoded')
            req.add_header('Origin', 'https://app.zonnedimmer.nl')
            req.add_header('Connection', 'keep-alive')
            req.add_header('Referer', login_page_url)
            req.add_header('Upgrade-Insecure-Requests', '1')

            response = self.opener.open(req, timeout=10)
            Domoticz.Log(f"Login POST response: HTTP {response.status}")
            Domoticz.Log(f"Login redirect to: {response.geturl()}")

            # Step 3: Store session cookies
            cookie_list = []
            for cookie in cookie_jar:
                cookie_list.append(f"{cookie.name}={cookie.value}")

            if cookie_list:
                self.session_cookie = "; ".join(cookie_list)
                Domoticz.Log(f"Session cookies stored: {len(cookie_list)} cookies, {len(self.session_cookie)} chars total")
                for cookie in cookie_jar:
                    Domoticz.Debug(f"  Cookie: {cookie.name} = {cookie.value[:20]}... (expires: {cookie.expires or 'session'})")
            else:
                Domoticz.Error("No session cookies received from login!")

            # Step 4: Try to get bearer token from API
            # After login, we can access API endpoints
            Domoticz.Log("Getting API bearer token...")

            dashboard_url = "https://app.zonnedimmer.nl/dashboard"
            req = urllib.request.Request(dashboard_url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0')
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
            req.add_header('Accept-Language', 'en-GB,en;q=0.9')
            req.add_header('Accept-Encoding', 'gzip, deflate')
            req.add_header('Connection', 'keep-alive')
            response = self.opener.open(req, timeout=10)
            Domoticz.Log(f"Dashboard loaded: HTTP {response.status}")

            # Check response headers for bearer token
            Domoticz.Debug("Checking response headers for bearer token...")
            headers = response.info()
            for header_name, header_value in headers.items():
                Domoticz.Debug(f"  Header: {header_name} = {header_value[:100] if len(str(header_value)) > 100 else header_value}")
                if 'authorization' in header_name.lower() or 'bearer' in str(header_value).lower():
                    Domoticz.Log(f"Found auth header: {header_name} = {header_value}")

            dashboard_html = decompress_response(response.read())
            Domoticz.Debug(f"Dashboard HTML size: {len(dashboard_html)} bytes")

            # Try multiple patterns to extract bearer token from HTML
            token_patterns = [
                r'localStorage\.setItem\(["\']token["\']\s*,\s*["\']([^"\']+)["\']',  # localStorage.setItem("token", "123|abc")
                r'Bearer\s+([\d]+\|[\w]+)',  # Original pattern: Bearer 123|abc
                r'bearer["\s:]+([0-9]+\|[a-zA-Z0-9]+)',  # bearer: "123|abc" or bearer":"123|abc"
                r'Authorization["\s:]+Bearer\s+([0-9]+\|[a-zA-Z0-9]+)',  # Authorization: Bearer 123|abc
                r'"token"["\s:]+([0-9]+\|[a-zA-Z0-9]+)',  # "token":"123|abc"
                r'api[_-]?token["\s:]+([0-9]+\|[a-zA-Z0-9]+)',  # api_token or api-token
            ]

            for pattern in token_patterns:
                token_match = re.search(pattern, dashboard_html, re.IGNORECASE)
                if token_match:
                    self.bearer_token = token_match.group(1)
                    Domoticz.Log(f"Bearer token obtained using pattern '{pattern}': {self.bearer_token[:20]}...")
                    Domoticz.Log(f"Bearer token length: {len(self.bearer_token)} characters")
                    break

            if not self.bearer_token:
                # Bearer token might be set via API call, store session for now
                Domoticz.Log("Bearer token not found in page, will use session authentication")
                Domoticz.Debug("Searching for 'Bearer' in dashboard HTML...")
                bearer_lower = dashboard_html.lower()
                if 'bearer' in bearer_lower:
                    # Find all occurrences and log context
                    index = 0
                    occurrences = 0
                    while True:
                        index = bearer_lower.find('bearer', index)
                        if index == -1:
                            break
                        occurrences += 1
                        # Log 100 chars before and after
                        start = max(0, index - 50)
                        end = min(len(dashboard_html), index + 100)
                        context = dashboard_html[start:end].replace('\n', ' ').replace('\r', '')
                        Domoticz.Debug(f"Bearer occurrence #{occurrences} at pos {index}: ...{context}...")
                        index += 6  # Move past 'bearer'
                        if occurrences >= 5:  # Limit to first 5 occurrences
                            break
                    Domoticz.Debug(f"Found 'bearer' text {occurrences}+ times but no pattern matched")
                else:
                    Domoticz.Debug("No 'bearer' text found in dashboard HTML")

            Domoticz.Log("Login successful!")
            UpdateDevice(self.UNIT_STATUS_TEXT, 0, "Connected")

        except urllib.error.HTTPError as e:
            Domoticz.Error(f"HTTP Error during login: {e.code} - {e.reason}")
            Domoticz.Error(f"Failed URL: {e.url}")
            try:
                error_body = decompress_response(e.read())
                if len(error_body) < 500:
                    Domoticz.Error(f"Error response: {error_body}")
                else:
                    Domoticz.Error(f"Error response (first 500 chars): {error_body[:500]}...")
            except:
                Domoticz.Error("Could not read error response body")

            if e.code == 429:
                Domoticz.Log("Rate limited - will retry on next heartbeat cycle (wait 20 seconds)")
                UpdateDevice(self.UNIT_STATUS_TEXT, 0, "Rate limited - waiting")
            else:
                UpdateDevice(self.UNIT_STATUS_TEXT, 0, f"Login failed: {e.code}")
        except Exception as e:
            Domoticz.Error(f"Login failed with exception: {str(e)}")
            Domoticz.Error(f"Exception type: {type(e).__name__}")
            import traceback
            Domoticz.Error("Full traceback:")
            for line in traceback.format_exc().split('\n'):
                if line.strip():
                    Domoticz.Error(f"  {line}")
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
            req.add_header('Accept-Encoding', 'gzip, deflate')
            req.add_header('X-Requested-With', 'XMLHttpRequest')
            req.add_header('Connection', 'keep-alive')
            req.add_header('Referer', 'https://app.zonnedimmer.nl/dashboard')

            # Use bearer token if available, otherwise use session cookie
            if self.bearer_token:
                req.add_header('Authorization', f'Bearer {self.bearer_token}')

            if self.session_cookie:
                req.add_header('Cookie', self.session_cookie)

            # Use opener with cookies if available
            if self.opener:
                response = self.opener.open(req, timeout=10)
            else:
                response = urllib.request.urlopen(req, timeout=10)

            with response:
                Domoticz.Debug(f"Live data API response: HTTP {response.status}")
                response_text = decompress_response(response.read())
                Domoticz.Debug(f"Live data response size: {len(response_text)} bytes")
                data = json.loads(response_text)

                # Extract live power value
                if 'live' in data and len(data['live']) > 0:
                    power_kw = float(data['live'][0])
                    power = power_kw * 1000  # Convert kW to W
                    UpdateDevice(self.UNIT_LIVE_POWER, 0, str(power))
                    Domoticz.Log(f"Solar generation updated: {power:.0f}W ({power_kw:.3f}kW)")
                    UpdateDevice(self.UNIT_STATUS_TEXT, 0, f"OK - {power:.0f}W")
                else:
                    Domoticz.Log("Live data retrieved but no power values available")
                    Domoticz.Debug(f"Live data: {data.get('live', [])}")

        except urllib.error.HTTPError as e:
            Domoticz.Error(f"HTTP Error updating live data: {e.code} - {e.reason}")
            Domoticz.Error(f"API URL: {url}")
            if e.code == 401:
                Domoticz.Log("Authentication expired, will re-login on next cycle")
                self.bearer_token = None
                self.session_cookie = None
                self.login()
        except Exception as e:
            Domoticz.Error(f"Error updating live data: {str(e)}")
            Domoticz.Error(f"Exception type: {type(e).__name__}")
            import traceback
            Domoticz.Debug(traceback.format_exc())

    def update_dimming_settings(self, enabled, price, curtailment_perc=0):
        """Update dimming settings on ZonneDimmer

        Based on the actual API call:
        POST to /dashboard/settings with form data:
        - _token: CSRF token (needed for web forms)
        - dynamic_contract: 0 or 1 (for enabling dynamic pricing)
        - min_negative_price_cts: price threshold in cents (e.g., -5 for -0.05 EUR)
        - curtailment_min_perc: curtailment percentage (0-100)
        """
        if not self.bearer_token and not self.session_cookie:
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
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0')
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
            req.add_header('Accept-Language', 'en-GB,en;q=0.9')
            req.add_header('Accept-Encoding', 'gzip, deflate')
            req.add_header('Content-Type', 'application/x-www-form-urlencoded')
            req.add_header('Origin', 'https://app.zonnedimmer.nl')
            req.add_header('Connection', 'keep-alive')
            req.add_header('Referer', 'https://app.zonnedimmer.nl/dashboard/settings')
            req.add_header('Upgrade-Insecure-Requests', '1')

            # Use cookie-based authentication (session)
            if hasattr(self, 'session_cookie') and self.session_cookie:
                req.add_header('Cookie', self.session_cookie)

            # Use opener with cookies
            if self.opener:
                response = self.opener.open(req, timeout=10)
            else:
                response = urllib.request.urlopen(req, timeout=10)

            with response:
                Domoticz.Log(f"Settings update response: HTTP {response.status}")
                Domoticz.Log(f"Settings updated successfully:")
                Domoticz.Log(f"  Dynamic contract: {'Enabled' if enabled else 'Disabled'}")
                Domoticz.Log(f"  Price threshold: {price:.3f} EUR/kWh ({price_cents} cents)")
                if curtailment_perc > 0:
                    Domoticz.Log(f"  Curtailment: {curtailment_perc}%")
                else:
                    Domoticz.Log(f"  Curtailment: Automatic")
                UpdateDevice(self.UNIT_STATUS_TEXT, 0, "Settings updated")

        except urllib.error.HTTPError as e:
            Domoticz.Error(f"HTTP Error updating settings: {e.code} - {e.reason}")
            Domoticz.Error(f"Settings URL: {url}")
            try:
                error_body = e.read().decode('utf-8')
                if len(error_body) < 300:
                    Domoticz.Error(f"Error response: {error_body}")
            except:
                pass
            if e.code == 401 or e.code == 419:  # 419 = CSRF token mismatch
                Domoticz.Error("Authentication or CSRF token error. Will re-login.")
                self.bearer_token = None
                self.session_cookie = None
                self.login()
            UpdateDevice(self.UNIT_STATUS_TEXT, 0, f"Settings failed: {e.code}")
        except Exception as e:
            Domoticz.Error(f"Error updating settings: {str(e)}")
            Domoticz.Error(f"Exception type: {type(e).__name__}")
            import traceback
            Domoticz.Debug(traceback.format_exc())
            UpdateDevice(self.UNIT_STATUS_TEXT, 0, f"Settings error")

    def get_csrf_token(self):
        """Get CSRF token from settings page"""
        try:
            url = "https://app.zonnedimmer.nl/dashboard/settings"

            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0')
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
            req.add_header('Accept-Language', 'en-GB,en;q=0.9')
            req.add_header('Accept-Encoding', 'gzip, deflate')
            req.add_header('Connection', 'keep-alive')
            req.add_header('Referer', 'https://app.zonnedimmer.nl/dashboard')

            if hasattr(self, 'session_cookie') and self.session_cookie:
                req.add_header('Cookie', self.session_cookie)

            # Use opener with cookies
            if self.opener:
                response = self.opener.open(req, timeout=10)
            else:
                response = urllib.request.urlopen(req, timeout=10)

            with response:
                Domoticz.Debug(f"Settings page response: HTTP {response.status}")
                html = decompress_response(response.read())
                Domoticz.Debug(f"Settings page HTML size: {len(html)} bytes")

                # Extract CSRF token from HTML
                # Look for: <input type="hidden" name="_token" value="...">
                import re
                match = re.search(r'name="_token"\s+value="([^"]+)"', html)
                if match:
                    token = match.group(1)
                    Domoticz.Log(f"CSRF token from settings page: {token[:20]}...")
                    return token

                Domoticz.Error("Could not find CSRF token in settings page")
                if '_token' in html:
                    Domoticz.Debug("Found '_token' text but pattern didn't match")
                else:
                    Domoticz.Debug("No '_token' text found in HTML")
                return None

        except Exception as e:
            Domoticz.Error(f"Error getting CSRF token: {str(e)}")
            Domoticz.Error(f"Exception type: {type(e).__name__}")
            import traceback
            Domoticz.Debug(traceback.format_exc())
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
