"""
9router OAuth Device Code Flow Integration

This module handles the OAuth device code flow for automatic account export to 9router.
Instead of registering directly and exporting tokens later, this flow:
1. Gets a device code from 9router
2. Uses the verification URL for registration/authorization
3. Polls 9router to finalize the account import

Flow:
    1. Login to 9router → get auth_token (cached in config)
    2. Create temp email
    3. Get device-code → verification_uri_complete + credentials
    4. Navigate to verification_uri_complete
    5. Complete AWS Builder ID registration (if no session)
    6. Browser authorizes Kiro AI for the account
    7. Poll 9router API → account automatically exported
    8. Store in local database

Requires sequential processing (no parallel registrations) due to device-code constraints.
"""

import json
import time
import urllib.request
import urllib.error
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone


class Router9OAuthClient:
    """Client for 9router OAuth device code flow API."""

    def __init__(self, base_url: str, password: str):
        """
        Initialize the 9router OAuth client.

        Args:
            base_url: 9router API base URL (e.g., https://xapi.fastev.my.id)
            password: 9router account password
        """
        self.base_url = base_url.rstrip('/')
        self.password = password
        self.auth_token = None
        self.auth_token_expires_at = None

    def login(self, log=print) -> Dict:
        """
        Authenticate with 9router and get auth token.

        The auth token is extracted from the 'Set-Cookie' response header
        (not from the response body) and is valid for ~24 hours.

        Args:
            log: Logging function

        Returns:
            {"ok": True, "auth_token": "...", "expires_at": "2026-07-10T10:00:00Z"}
            or {"ok": False, "error": "..."}
        """
        url = f"{self.base_url}/api/auth/login"
        payload = json.dumps({"password": self.password}).encode('utf-8')

        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    'Content-Type': 'application/json',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0',
                },
                method='POST'
            )

            log(f"Authenticating with 9router at {self.base_url}...", "info")
            log(f"Request URL: {url}", "dbg")

            with urllib.request.urlopen(req, timeout=30) as response:
                # Extract auth_token from Set-Cookie header
                set_cookie = response.headers.get('Set-Cookie', '')

                if not set_cookie or 'auth_token=' not in set_cookie:
                    return {"ok": False, "error": "No auth_token in response cookies"}

                # Parse: auth_token=VALUE; Path=/; ...
                auth_token = None
                for cookie_part in set_cookie.split(';'):
                    cookie_part = cookie_part.strip()
                    if cookie_part.startswith('auth_token='):
                        auth_token = cookie_part.split('=', 1)[1]
                        break

                if not auth_token:
                    return {"ok": False, "error": "Failed to parse auth_token from cookie"}

                # Assume token valid for ~24 hours
                expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

                self.auth_token = auth_token
                self.auth_token_expires_at = expires_at

                log(f"✅ Authentication successful (token expires: {expires_at})", "ok")

                return {
                    "ok": True,
                    "auth_token": auth_token,
                    "expires_at": expires_at
                }

        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8', errors='ignore')

            # Enhanced error logging
            log(f"❌ Login failed: HTTP {e.code} from {url}", "error")

            # Log response headers for debugging
            if hasattr(e, 'headers'):
                server = e.headers.get('Server', 'unknown')
                cf_ray = e.headers.get('CF-RAY', '')
                log(f"   Server: {server}" + (f" | CF-RAY: {cf_ray}" if cf_ray else ""), "dbg")

            # Parse error body if JSON
            try:
                error_data = json.loads(error_body)
                log(f"   Error response: {json.dumps(error_data, indent=2)}", "error")
                error_msg = error_data.get('message', error_data.get('error', str(error_data)))
            except:
                error_msg = error_body
                log(f"   Error body: {error_body}", "error")

            # Provide helpful context for common errors
            if e.code == 403:
                if 'cloudflare' in str(e.headers).lower() or 'error code: 1010' in error_body:
                    log("   💡 Cloudflare blocked this request (error 1010 = access denied)", "warn")
                    log("   Possible causes:", "info")
                    log("      - IP/country restriction", "info")
                    log("      - Bot detection (try with residential proxy)", "info")
                    log("      - Cloudflare firewall rules", "info")
                    log("      - Rate limiting", "info")
                elif 'password' in error_body.lower() or 'credentials' in error_body.lower():
                    log("   💡 Authentication failed - check router9_password in config", "warn")

            return {"ok": False, "error": f"HTTP {e.code}: {error_msg}"}

        except Exception as e:
            log(f"❌ Login failed: {str(e)}", "error")
            log(f"   Exception type: {type(e).__name__}", "dbg")
            return {"ok": False, "error": str(e)}

    def ensure_auth_token(self, cached_token: Optional[str] = None,
                          cached_expires_at: Optional[str] = None,
                          log=print) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Ensure we have a valid auth token (use cached or login).

        Args:
            cached_token: Previously cached auth token from config
            cached_expires_at: ISO timestamp when cached token expires
            log: Logging function

        Returns:
            (is_valid, auth_token, expires_at)
        """
        # Check if cached token is still valid
        if cached_token and cached_expires_at:
            try:
                expires_at = datetime.fromisoformat(cached_expires_at.replace('Z', '+00:00'))
                now = datetime.now(timezone.utc)

                # If token expires in >1 hour, use it
                if expires_at > now + timedelta(hours=1):
                    log("Using cached 9router auth token", "info")
                    self.auth_token = cached_token
                    self.auth_token_expires_at = cached_expires_at
                    return (True, cached_token, cached_expires_at)
                else:
                    log("Cached auth token expired or expiring soon, re-authenticating...", "warn")
            except Exception as e:
                log(f"Failed to parse cached token expiry: {e}", "warn")

        # Login to get new token
        result = self.login(log)

        if not result["ok"]:
            return (False, None, None)

        return (True, result["auth_token"], result["expires_at"])

    def get_device_code(self, log=print) -> Dict:
        """
        Get OAuth device code for account registration.

        This device code is used to link the AWS Builder ID registration
        with 9router. The returned verification_uri_complete is where
        the browser should navigate for registration/authorization.

        Args:
            log: Logging function

        Returns:
            {
                "ok": True,
                "device_code": "...",
                "user_code": "LMFN-FXLB",
                "verification_uri": "https://view.awsapps.com/start/#/device",
                "verification_uri_complete": "https://view.awsapps.com/start/#/device?user_code=...",
                "expires_in": 600,
                "interval": 1,
                "_clientId": "...",
                "_clientSecret": "...",
                "_region": "us-east-1",
                "_authMethod": "builder-id",
                "_startUrl": "https://view.awsapps.com/start",
                "codeVerifier": "..."
            }
            or {"ok": False, "error": "..."}
        """
        if not self.auth_token:
            return {"ok": False, "error": "Not authenticated (call ensure_auth_token first)"}

        url = f"{self.base_url}/api/oauth/kiro/device-code"

        try:
            req = urllib.request.Request(
                url,
                headers={
                    'Cookie': f'auth_token={self.auth_token}',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0',
                    'Accept': 'application/json, text/plain, */*',
                    'Accept-Language': 'en-US,en;q=0.9',
                },
                method='GET'
            )

            log("Requesting device code from 9router...", "info")
            log(f"Request URL: {url}", "dbg")

            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode('utf-8'))

                # Validate required fields
                required_fields = [
                    'device_code', 'verification_uri_complete',
                    '_clientId', '_clientSecret', 'codeVerifier'
                ]

                for field in required_fields:
                    if field not in data:
                        return {"ok": False, "error": f"Missing required field: {field}"}

                log(f"✅ Device code obtained (user_code: {data.get('user_code', 'N/A')})", "ok")
                log(f"   Expires in: {data.get('expires_in', 0)} seconds", "info")

                return {"ok": True, **data}

        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8', errors='ignore')

            # Enhanced error logging
            log(f"❌ Get device-code failed: HTTP {e.code} from {url}", "error")

            # Log response headers for debugging
            if hasattr(e, 'headers'):
                server = e.headers.get('Server', 'unknown')
                cf_ray = e.headers.get('CF-RAY', '')
                log(f"   Server: {server}" + (f" | CF-RAY: {cf_ray}" if cf_ray else ""), "dbg")

            # Parse error body if JSON
            try:
                error_data = json.loads(error_body)
                log(f"   Error response: {json.dumps(error_data, indent=2)}", "error")
                error_msg = error_data.get('message', error_data.get('error', str(error_data)))
            except:
                error_msg = error_body
                log(f"   Error body: {error_body}", "error")

            # Provide helpful context for common errors
            if e.code == 403:
                if 'cloudflare' in str(e.headers).lower() or 'error code: 1010' in error_body:
                    log("   💡 Cloudflare blocked this request (error 1010 = access denied)", "warn")
                    log("   Possible causes:", "info")
                    log("      - Auth token not accepted (check Cookie header format)", "info")
                    log("      - IP/country restriction", "info")
                    log("      - Bot detection (try with residential proxy)", "info")
                elif 'auth' in error_body.lower() or 'unauthorized' in error_body.lower():
                    log("   💡 Authentication issue - token may be invalid or expired", "warn")

            return {"ok": False, "error": f"HTTP {e.code}: {error_msg}"}

        except Exception as e:
            log(f"❌ Get device-code failed: {str(e)}", "error")
            log(f"   Exception type: {type(e).__name__}", "dbg")
            return {"ok": False, "error": str(e)}

    def poll_account(self, device_code: str, client_id: str,
                     client_secret: str, code_verifier: str,
                     log=print, max_retries: int = 3) -> Dict:
        """
        Poll 9router to finalize account export after registration.

        This should be called after the user has successfully completed
        AWS Builder ID registration and authorized Kiro AI.

        Args:
            device_code: Device code from get_device_code()
            client_id: Client ID from get_device_code()
            client_secret: Client secret from get_device_code()
            code_verifier: Code verifier from get_device_code()
            log: Logging function
            max_retries: Maximum retry attempts for retryable errors

        Returns:
            {"ok": True} or {"ok": False, "error": "...", "retryable": bool}
        """
        if not self.auth_token:
            return {"ok": False, "error": "Not authenticated", "retryable": False}

        url = f"{self.base_url}/api/oauth/kiro/poll"
        payload = json.dumps({
            "deviceCode": device_code,
            "extraData": {
                "_clientId": client_id,
                "_clientSecret": client_secret
            },
            "codeVerifier": code_verifier
        }).encode('utf-8')

        for attempt in range(1, max_retries + 1):
            try:
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={
                        'Content-Type': 'application/json',
                        'Cookie': f'auth_token={self.auth_token}',
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
                    },
                    method='POST'
                )

                log(f"Polling 9router to finalize account export (attempt {attempt}/{max_retries})...", "info")

                with urllib.request.urlopen(req, timeout=60) as response:
                    response_data = response.read().decode('utf-8')

                    try:
                        result = json.loads(response_data)
                    except json.JSONDecodeError:
                        result = {"message": response_data}

                    log(f"✅ Account successfully exported to 9router!", "ok")

                    return {"ok": True, "response": result}

            except urllib.error.HTTPError as e:
                error_body = e.read().decode('utf-8', errors='ignore')

                # Enhanced error logging
                log(f"❌ Poll failed: HTTP {e.code} from {url}", "error")

                # Log response headers for debugging
                if hasattr(e, 'headers'):
                    server = e.headers.get('Server', 'unknown')
                    cf_ray = e.headers.get('CF-RAY', '')
                    log(f"   Server: {server}" + (f" | CF-RAY: {cf_ray}" if cf_ray else ""), "dbg")

                # Parse error body if JSON
                try:
                    error_data = json.loads(error_body)
                    log(f"   Error response: {json.dumps(error_data, indent=2)}", "error")
                    error_msg = error_data.get('message', error_data.get('error', str(error_data)))
                except:
                    error_msg = error_body
                    log(f"   Error body: {error_body}", "error")

                # Provide helpful context for common errors
                if e.code == 403:
                    if 'cloudflare' in str(e.headers).lower() or 'error code: 1010' in error_body:
                        log("   💡 Cloudflare blocked this request (error 1010 = access denied)", "warn")
                        log("   Possible causes:", "info")
                        log("      - Auth token not accepted (check Cookie header format)", "info")
                        log("      - IP/country restriction", "info")
                        log("      - Bot detection (try with residential proxy)", "info")
                        log("      - 9router server may require requests from specific IP ranges", "info")
                    elif 'auth' in error_body.lower() or 'unauthorized' in error_body.lower():
                        log("   💡 Authentication issue - token may be invalid or expired", "warn")
                elif e.code == 401:
                    log("   💡 Authentication failed - auth_token may be invalid or expired", "warn")
                    log("      Try re-authenticating to get a fresh token", "info")

                # Check if error is retryable
                retryable = e.code in [408, 429, 500, 502, 503, 504]

                if retryable and attempt < max_retries:
                    wait_time = 5  # Fixed 5s delay (like grok)
                    log(f"   Retrying in {wait_time} seconds (attempt {attempt + 1}/{max_retries})...", "warn")
                    time.sleep(wait_time)
                    continue

                return {
                    "ok": False,
                    "error": f"HTTP {e.code}: {error_msg}",
                    "retryable": retryable
                }

            except Exception as e:
                log(f"Poll failed: {str(e)}", "error")

                # Network errors are retryable
                retryable = True

                if attempt < max_retries:
                    wait_time = 5  # Fixed 5s delay (like grok)
                    log(f"Retrying in {wait_time} seconds...", "warn")
                    time.sleep(wait_time)
                    continue

                return {
                    "ok": False,
                    "error": str(e),
                    "retryable": retryable
                }

        return {
            "ok": False,
            "error": f"Failed after {max_retries} attempts",
            "retryable": False
        }