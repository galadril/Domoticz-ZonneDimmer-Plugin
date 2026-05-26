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
        self.xsrf_token = None
        self.session_cookie = None
        self.opener = None
        self.dimming_enabled = False
        self.dim_price = 0.0
        self.curtailment_perc = 0
        self.energy_supplier_id = None
        self.exclude_tax = "0"
        self.heartbeat_counter = 0
        self.login_retry_counter = 0
        self.auth_failed_counter = 0
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

        # Replace device if wrong type or missing step/unit options
        price_opts = {"ValueStep": "0.1", "ValueMin": "-1.0", "ValueMax": "1.0", "ValueUnit": "\u20ac/kWh"}
        if self.UNIT_PRICE_DIMMER in Devices:
            d = Devices[self.UNIT_PRICE_DIMMER]
            if d.Type != 242 or d.Options.get("ValueStep") != "0.1":
                d.Delete()
                Domoticz.Log("Price device recreated with step/unit options.")
        if self.UNIT_PRICE_DIMMER not in Devices:
            Domoticz.Device(Name="Dim Price Threshold", Unit=self.UNIT_PRICE_DIMMER, Type=242, Subtype=1, Used=0, Options=price_opts).Create()
            Domoticz.Log("Price Setpoint device created.")

        if self.UNIT_LIVE_POWER not in Devices:
            Domoticz.Device(Name="Solar Generation", Unit=self.UNIT_LIVE_POWER, TypeName="Usage", Used=0).Create()
            Domoticz.Log("Live Power device created.")

        if self.UNIT_STATUS_TEXT not in Devices:
            Domoticz.Device(Name="Status", Unit=self.UNIT_STATUS_TEXT, TypeName="Text", Used=0).Create()
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
                UpdateDevice(self.UNIT_DIMMING_SWITCH, 1, "On")
                Domoticz.Log("Dimming enabled")
            elif Command == "Off":
                self.dimming_enabled = False
                self.update_dimming_settings(False, self.dim_price, self.curtailment_perc)
                UpdateDevice(self.UNIT_DIMMING_SWITCH, 0, "Off")
                Domoticz.Log("Dimming disabled")

        elif Unit == self.UNIT_PRICE_DIMMER:
            # Price setpoint: Level IS the EUR/kWh value directly (e.g. -0.05)
            self.dim_price = float(Level)
            self.update_dimming_settings(self.dimming_enabled, self.dim_price, self.curtailment_perc)
            UpdateDevice(self.UNIT_PRICE_DIMMER, 0, f"{self.dim_price:.2f}")
            Domoticz.Log(f"Dim price threshold set to: {self.dim_price:.2f} EUR/kWh")

        elif Unit == self.UNIT_CURTAILMENT_DIMMER:
            # Curtailment percentage dimmer: Level 0-100 directly maps to 0-100%
            self.curtailment_perc = Level
            self.update_dimming_settings(self.dimming_enabled, self.dim_price, self.curtailment_perc)
            UpdateDevice(self.UNIT_CURTAILMENT_DIMMER, 2 if Level > 0 else 0, str(Level))
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

            Domoticz.Log("Getting login page...")
            UpdateDevice(self.UNIT_STATUS_TEXT, 0, "Logging in...")

            login_page_url = "https://app.zonnedimmer.nl/login"

            # Create cookie jar to handle cookies
            cookie_jar = http.cookiejar.CookieJar()
            self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

            # Step 1: Get Sanctum CSRF cookie (sets XSRF-TOKEN cookie for SPA auth)
            try:
                Domoticz.Log("Fetching Sanctum CSRF cookie...")
                sanctum_url = "https://app.zonnedimmer.nl/sanctum/csrf-cookie"
                req = urllib.request.Request(sanctum_url)
                req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0')
                req.add_header('Accept', 'application/json, text/plain, */*')
                req.add_header('Referer', 'https://app.zonnedimmer.nl/')
                self.opener.open(req, timeout=10)
                for cookie in cookie_jar:
                    if cookie.name == 'XSRF-TOKEN':
                        self.xsrf_token = urllib.parse.unquote(cookie.value)
                        Domoticz.Log(f"XSRF-TOKEN obtained: {self.xsrf_token[:20]}...")
                        break
                if not self.xsrf_token:
                    Domoticz.Debug("No XSRF-TOKEN cookie received from Sanctum endpoint")
            except Exception as e:
                Domoticz.Debug(f"Sanctum CSRF cookie fetch failed (non-fatal): {str(e)}")

            # Step 2: Get login page to obtain CSRF token

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
            if self.xsrf_token:
                req.add_header('X-XSRF-TOKEN', self.xsrf_token)

            response = self.opener.open(req, timeout=10)
            Domoticz.Log(f"Login POST response: HTTP {response.status}")
            Domoticz.Log(f"Login redirect to: {response.geturl()}")

            # Read the response body immediately — this IS the /dashboard HTML after the 302 redirect.
            # The token is injected via Laravel session flash data which is only present on this
            # first visit. A subsequent explicit GET to /dashboard will NOT have the token.
            login_dashboard_html = decompress_response(response.read())
            Domoticz.Debug(f"Login redirect HTML size: {len(login_dashboard_html)} bytes")

            # Step 3: Store session cookies and refresh XSRF token if updated
            cookie_list = []
            for cookie in cookie_jar:
                cookie_list.append(f"{cookie.name}={cookie.value}")

            if cookie_list:
                self.session_cookie = "; ".join(cookie_list)
                Domoticz.Log(f"Session cookies stored: {len(cookie_list)} cookies, {len(self.session_cookie)} chars total")
                for cookie in cookie_jar:
                    Domoticz.Debug(f"  Cookie: {cookie.name} = {cookie.value[:20]}... (expires: {cookie.expires or 'session'})")
                    if cookie.name == 'XSRF-TOKEN':
                        self.xsrf_token = urllib.parse.unquote(cookie.value)
                        Domoticz.Log(f"XSRF-TOKEN refreshed after login: {self.xsrf_token[:20]}...")
            else:
                Domoticz.Error("No session cookies received from login!")

            # Step 4a: Try REST API login to get token directly (JSON endpoint)
            try:
                Domoticz.Log("Trying REST API login for bearer token...")
                api_login_url = "https://app.zonnedimmer.nl/api/login"
                login_payload = json.dumps({"email": self.email, "password": self.password}).encode('utf-8')
                req = urllib.request.Request(api_login_url, data=login_payload, method='POST')
                req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0')
                req.add_header('Content-Type', 'application/json')
                req.add_header('Accept', 'application/json')
                req.add_header('X-Requested-With', 'XMLHttpRequest')
                response = self.opener.open(req, timeout=10)
                api_data = json.loads(decompress_response(response.read()))
                Domoticz.Debug(f"REST API login response: {str(api_data)[:200]}")
                for key in ('token', 'access_token', 'bearer_token', 'api_token'):
                    if key in api_data:
                        self.bearer_token = api_data[key]
                        Domoticz.Log(f"Bearer token obtained from REST API login ({key}): {self.bearer_token[:20]}...")
                        self.auth_failed_counter = 0
                        break
                if not self.bearer_token and 'data' in api_data and isinstance(api_data['data'], dict):
                    for key in ('token', 'access_token'):
                        if key in api_data['data']:
                            self.bearer_token = api_data['data'][key]
                            Domoticz.Log(f"Bearer token obtained from REST API login data.{key}: {self.bearer_token[:20]}...")
                            self.auth_failed_counter = 0
                            break
            except Exception as e:
                Domoticz.Debug(f"REST API login attempt failed: {str(e)}")

            # Step 4b: Extract bearer token from the login redirect HTML (dashboard page).
            # Use the already-read body — do NOT make a second GET to /dashboard as that
            # would miss the token (Laravel flash data already consumed on the first visit).
            Domoticz.Log("Extracting bearer token from login redirect HTML...")
            dashboard_html = login_dashboard_html
            Domoticz.Debug(f"Dashboard HTML size: {len(dashboard_html)} bytes")

            # Try multiple patterns to extract bearer token from HTML
            token_patterns = [
                # localStorage.setItem('token', '...') — any format, at least 10 chars
                r'localStorage\.setItem\s*\(\s*["\']token["\']\s*,\s*["\']([^"\']{10,})["\']',
                # window.token / window.apiToken / window.authToken
                r'window\.(?:token|apiToken|authToken|bearerToken)\s*=\s*["\']([^"\']{10,})["\']',
                # meta tag: <meta name="api-token" content="...">
                r'<meta[^>]+name=["\'](?:api-token|bearer-token|token)["\'][^>]+content=["\']([^"\']{10,})["\']',
                r'<meta[^>]+content=["\']([^"\']{10,})["\'][^>]+name=["\'](?:api-token|bearer-token|token)["\']',
                # JSON: "token": "..." or "access_token": "..."
                r'"(?:token|access_token|api_token)"\s*:\s*"([^"]{10,})"',
                # Sanctum/Passport number|hash format
                r'["\']([0-9]{1,6}\|[a-zA-Z0-9]{20,})["\']',
                # Bearer header values in HTML
                r'Bearer\s+([\w.|-]{10,})',
            ]

            for pattern in token_patterns:
                token_match = re.search(pattern, dashboard_html, re.IGNORECASE)
                if token_match:
                    self.bearer_token = token_match.group(1)
                    Domoticz.Log(f"Bearer token obtained using pattern: {self.bearer_token[:20]}...")
                    Domoticz.Log(f"Bearer token length: {len(self.bearer_token)} characters")
                    self.auth_failed_counter = 0  # Reset auth failure counter on successful token extraction
                    break

            if not self.bearer_token:
                # Bearer token might be set via API call, store session for now
                Domoticz.Log("Bearer token not found in page, will use session authentication")

                # Search for the exact script tag that should contain the token
                Domoticz.Debug("Searching for token in <script> tags...")
                html_lower = dashboard_html.lower()

                # Look for <script> tags in the HTML
                script_start = html_lower.find('<body')
                if script_start != -1:
                    script_section = dashboard_html[script_start:script_start+1000]
                    Domoticz.Debug(f"First 1000 chars after <body>: {script_section[:500]}")

                # Search for localStorage.setItem with token
                if 'localstorage' in html_lower:
                    # Find ALL localStorage occurrences (not just those near setItem)
                    ls_index = html_lower.find('localstorage')
                    while ls_index != -1:
                        # Get 600 chars after each occurrence
                        start = max(0, ls_index - 150)
                        end = min(len(dashboard_html), ls_index + 600)
                        context = dashboard_html[start:end]
                        # Replace newlines so Domoticz doesn't truncate log output
                        context_flat = context.replace('\n', ' ').replace('\r', '')

                        Domoticz.Debug(f"localStorage at pos {ls_index} (flat): {context_flat[:400]}")

                        if 'setitem' in context.lower():
                            # Try to extract token — quoted string (single, double, or backtick)
                            token_match = re.search(
                                r'setItem\s*\(\s*["\']token["\']\s*,\s*["\'\`]([^"\'\`]{10,})["\'\`]',
                                context, re.IGNORECASE)
                            if token_match:
                                self.bearer_token = token_match.group(1)
                                Domoticz.Log(f"Found token in localStorage.setItem: {self.bearer_token[:20]}...")
                                self.auth_failed_counter = 0
                                break

                        # Find next occurrence
                        ls_index = html_lower.find('localstorage', ls_index + 1)

                # Also search for the Sanctum token format anywhere in the HTML
                if not self.bearer_token:
                    sanctum_match = re.search(r'["\'\`]([0-9]{1,6}\|[a-zA-Z0-9]{20,})["\'\`]', dashboard_html)
                    if sanctum_match:
                        self.bearer_token = sanctum_match.group(1)
                        Domoticz.Log(f"Found Sanctum token pattern in HTML: {self.bearer_token[:20]}...")
                        self.auth_failed_counter = 0

                # If still not found, log end-of-HTML for diagnostics (token scripts injected near </body>)
                if not self.bearer_token:
                    Domoticz.Debug("No token found via localStorage/Sanctum search.")
                    Domoticz.Debug(f"HTML end (last 1000 chars flat): {dashboard_html[-1000:].replace(chr(10), ' ').replace(chr(13), '')}")

            # If no bearer token found in HTML, try several API endpoints
            if not self.bearer_token:
                api_token_endpoints = [
                    "https://app.zonnedimmer.nl/api/user",
                    "https://app.zonnedimmer.nl/api/v1/user",
                    "https://app.zonnedimmer.nl/api/auth/me",
                    "https://app.zonnedimmer.nl/api/me",
                ]
                for endpoint_url in api_token_endpoints:
                    try:
                        Domoticz.Log(f"Trying token endpoint: {endpoint_url}")
                        req = urllib.request.Request(endpoint_url)
                        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0')
                        req.add_header('Accept', 'application/json')
                        req.add_header('X-Requested-With', 'XMLHttpRequest')
                        req.add_header('Referer', 'https://app.zonnedimmer.nl/dashboard')
                        response = self.opener.open(req, timeout=10)
                        endpoint_data = json.loads(decompress_response(response.read()))
                        Domoticz.Debug(f"Response from {endpoint_url}: {str(endpoint_data)[:200]}")
                        for key in ('token', 'access_token', 'api_token', 'bearer_token'):
                            if key in endpoint_data:
                                self.bearer_token = endpoint_data[key]
                                Domoticz.Log(f"Bearer token obtained from {endpoint_url} ({key}): {self.bearer_token[:20]}...")
                                self.auth_failed_counter = 0
                                break
                        if self.bearer_token:
                            break
                    except Exception as e:
                        Domoticz.Debug(f"Could not fetch token from {endpoint_url}: {str(e)}")

            Domoticz.Log("Login successful!")
            self.login_retry_counter = 0  # Reset retry counter on successful login
            UpdateDevice(self.UNIT_STATUS_TEXT, 0, "Connected")
            self.fetch_current_settings()

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

    def fetch_current_settings(self):
        """Fetch current settings from ZonneDimmer and sync Domoticz devices to match."""
        if not self.bearer_token and not self.session_cookie:
            return

        try:
            url = "https://app.zonnedimmer.nl/dashboard/settings"
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0')
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
            req.add_header('Accept-Encoding', 'gzip, deflate')
            req.add_header('Referer', 'https://app.zonnedimmer.nl/dashboard')
            if self.bearer_token:
                req.add_header('Authorization', f'Bearer {self.bearer_token}')
            if self.session_cookie:
                req.add_header('Cookie', self.session_cookie)

            if self.opener:
                response = self.opener.open(req, timeout=10)
            else:
                response = urllib.request.urlopen(req, timeout=10)

            with response:
                html = decompress_response(response.read())

            # dynamic_contract: hidden=0 + checkbox value=1 checked → enabled when checkbox has 'checked'
            dyn_match = re.search(
                r'name="dynamic_contract"[^>]*value="1"[^>]*checked|'
                r'name="dynamic_contract"[^>]*checked[^>]*value="1"',
                html, re.IGNORECASE | re.DOTALL)
            enabled = bool(dyn_match)

            # min_negative_price_cts: <input ... name="min_negative_price_cts" ... value="-5" ...>
            price_match = re.search(r'name="min_negative_price_cts"[^>]+value="([^"]+)"', html)
            price_cents = int(price_match.group(1)) if price_match else 0
            price_eur = price_cents / 100.0

            # curtailment_min_perc: <option value="30" selected> — absent means empty (0%)
            curt_block = re.search(r'name="curtailment_min_perc".*?</select>', html, re.IGNORECASE | re.DOTALL)
            curtailment = 0
            if curt_block:
                sel_opt = re.search(r'<option[^>]+value="([0-9]+)"[^>]*selected', curt_block.group(), re.IGNORECASE)
                if sel_opt:
                    curtailment = int(sel_opt.group(1))

            # energy_supplier_id: <option value="16" selected>
            supplier_block = re.search(r'name="energy_supplier_id".*?</select>', html, re.IGNORECASE | re.DOTALL)
            if supplier_block:
                sup_match = re.search(r'<option[^>]+value="([0-9]+)"[^>]*selected', supplier_block.group(), re.IGNORECASE)
                if sup_match:
                    self.energy_supplier_id = sup_match.group(1)

            # exclude_tax: radio button with checked attribute
            tax_match = re.search(r'name="exclude_tax"[^>]+value="([01])"[^>]*checked', html, re.IGNORECASE)
            if tax_match:
                self.exclude_tax = tax_match.group(1)

            Domoticz.Log(f"Current settings: enabled={enabled}, price={price_eur:.2f} EUR/kWh ({price_cents} cts), curtailment={curtailment}%, supplier={self.energy_supplier_id}, exclude_tax={self.exclude_tax}")

            # Update internal state
            self.dimming_enabled = enabled
            self.dim_price = price_eur
            self.curtailment_perc = curtailment

            # Sync Domoticz switch device: nValue 1=On, 0=Off
            UpdateDevice(self.UNIT_DIMMING_SWITCH, 1 if enabled else 0, "On" if enabled else "Off")

            # Sync price setpoint: sValue is the actual EUR/kWh value (e.g. "-0.05")
            UpdateDevice(self.UNIT_PRICE_DIMMER, 0, f"{price_eur:.2f}")

            # Sync curtailment dimmer: Level = curtailment percentage (0-100)
            UpdateDevice(self.UNIT_CURTAILMENT_DIMMER, 2 if curtailment > 0 else 0, str(curtailment))

        except Exception as e:
            Domoticz.Error(f"Error fetching current settings: {str(e)}")

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

            # Use bearer token if available, otherwise use Sanctum session auth
            if self.bearer_token:
                req.add_header('Authorization', f'Bearer {self.bearer_token}')
            elif self.xsrf_token:
                req.add_header('X-XSRF-TOKEN', self.xsrf_token)

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
                self.auth_failed_counter += 1
                Domoticz.Log(f"Authentication failed (attempt {self.auth_failed_counter}), will re-login on next cycle")
                # Only clear tokens after multiple failures to avoid rate limiting
                if self.auth_failed_counter >= 2:
                    Domoticz.Log("Multiple auth failures, clearing tokens and waiting for next heartbeat cycle")
                    self.bearer_token = None
                    self.xsrf_token = None
                    self.session_cookie = None
                else:
                    Domoticz.Log("Keeping tokens for retry on next cycle")
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

            # Prepare form data — matches the browser POST exactly.
            # dynamic_contract is sent twice: hidden=0 always, then checkbox=1 when enabled.
            # energy_supplier_id and exclude_tax must be included to avoid the server resetting them.
            form_pairs = [
                ('_token', csrf_token),
                ('dynamic_contract', '0'),           # hidden field (always)
            ]
            if enabled:
                form_pairs.append(('dynamic_contract', '1'))   # checkbox (only when checked)
            if self.energy_supplier_id:
                form_pairs.append(('energy_supplier_id', self.energy_supplier_id))
            form_pairs.append(('exclude_tax', self.exclude_tax))
            form_pairs.append(('min_negative_price_cts', str(price_cents)))
            form_pairs.append(('curtailment_min_perc', str(curtailment_perc) if curtailment_perc > 0 else ''))

            data = urllib.parse.urlencode(form_pairs).encode('utf-8')

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

            # Cookie-based authentication (session)
            if self.session_cookie:
                req.add_header('Cookie', self.session_cookie)
            if self.xsrf_token:
                req.add_header('X-XSRF-TOKEN', self.xsrf_token)

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
                self.xsrf_token = None
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

            if self.session_cookie:
                req.add_header('Cookie', self.session_cookie)
            if self.xsrf_token:
                req.add_header('X-XSRF-TOKEN', self.xsrf_token)

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
