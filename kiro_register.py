"""
Kiro auto-registration module: Playwright + HTTP hybrid.

The browser walks the full signin.aws -> profile.aws redirect chain, and once
registration completes tokens are injected locally so Kiro starts logged in.

Dependencies: curl_cffi, playwright, playwright-stealth, cryptography.
"""
import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import string
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse


# --- Constants ---------------------------------------------------------------

SHIROMAIL_BASE = os.environ.get("SHIROMAIL_BASE_URL", "")
SHIROMAIL_KEY = os.environ.get("SHIROMAIL_API_KEY", "")
SHIROMAIL_DOMAIN_ID = int(os.environ.get("SHIROMAIL_DOMAIN_ID", "0") or "0")

REG_OIDC = "https://oidc.us-east-1.amazonaws.com"
REG_SCOPES = [
    "codewhisperer:completions", "codewhisperer:analysis",
    "codewhisperer:conversations", "codewhisperer:transformations",
    "codewhisperer:taskassist",
]
REG_REDIRECT_URI = "http://127.0.0.1:3128"
KIRO_SIGNIN_URL = "https://app.kiro.dev/signin"
ISSUER_URL = "https://view.awsapps.com/start/"
REG_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

JWE_ALG = "RSA-OAEP-256"
JWE_ENC = "A256GCM"
JWE_CTY = "application/aws+signin+jwe"

_FIRST_NAMES = [
    "James", "Robert", "Michael", "William", "David", "Richard", "Joseph", "Thomas",
    "Christopher", "Daniel", "Matthew", "Anthony", "Mark", "Steven", "Andrew", "Paul",
    "Oliver", "Henry", "Samuel", "Benjamin", "Alexander", "Sebastian", "Elijah", "Owen",
    "Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Susan", "Jessica", "Sarah",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson",
    "Lee", "Thompson", "White", "Harris", "Clark", "Lewis", "Robinson", "Walker",
]


# --- Fingerprint randomisation ----------------------------------------------

_SCREEN_RESOLUTIONS = [
    (1920, 1080), (2560, 1440), (1366, 768), (1536, 864),
    (1440, 900), (1680, 1050), (1600, 900), (1280, 720),
    (1920, 1200), (2560, 1080), (3440, 1440), (1280, 1024),
]

_VIEWPORT_SIZES = [
    (1280, 800), (1366, 768), (1440, 900), (1536, 864),
    (1600, 900), (1680, 1050), (1920, 1080), (1280, 720),
]

_LOCALES = ["en-US", "en-GB", "en-CA", "en-AU"]

_TIMEZONES = [
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Toronto", "Europe/London",
]

_WEBGL_VENDORS = ["Google Inc. (NVIDIA)", "Google Inc. (AMD)", "Google Inc. (Intel)"]
_WEBGL_RENDERERS = [
    "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (AMD, AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)",
]

_CHROME_VERSIONS = [
    "131.0.6778.86", "131.0.6778.109", "132.0.6834.57", "132.0.6834.83",
    "133.0.6876.0", "130.0.6723.117", "131.0.6778.140",
]

_PLATFORM_VERSIONS = [
    "10.0.0", "10.0.1", "15.0.0", "14.6.1",
]


def _random_machine_ids():
    """Generate a plausibly-shaped set of random machine identifiers."""
    import uuid
    service_id = str(uuid.uuid4())
    mac_machine_id = secrets.token_hex(64)  # 128 hex chars (SHA-512 format)
    machine_id = secrets.token_hex(32)      # 64 hex chars (SHA-256 format)
    sqm_id = "{" + str(uuid.uuid4()).upper() + "}"
    return {
        "storage.serviceMachineId": service_id,
        "telemetry.devDeviceId": service_id,
        "telemetry.macMachineId": mac_machine_id,
        "telemetry.machineId": machine_id,
        "telemetry.sqmId": sqm_id,
    }


def _random_ua():
    """Generate a random Chrome user agent."""
    ver = secrets.choice(_CHROME_VERSIONS)
    return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{ver} Safari/537.36")


def _random_fingerprint_config():
    """Build a full set of randomised browser fingerprint parameters."""
    screen = secrets.choice(_SCREEN_RESOLUTIONS)
    viewport = secrets.choice(_VIEWPORT_SIZES)
    while viewport[0] > screen[0] or viewport[1] > screen[1]:
        viewport = secrets.choice(_VIEWPORT_SIZES)
    return {
        "viewport": {"width": viewport[0], "height": viewport[1]},
        "screen": {"width": screen[0], "height": screen[1]},
        "locale": secrets.choice(_LOCALES),
        "timezone": secrets.choice(_TIMEZONES),
        "user_agent": _random_ua(),
        "webgl_vendor": secrets.choice(_WEBGL_VENDORS),
        "webgl_renderer": secrets.choice(_WEBGL_RENDERERS),
        "hardware_concurrency": secrets.choice([4, 6, 8, 12, 16]),
        "device_memory": secrets.choice([4, 8, 16, 32]),
        "color_depth": 24,
        "pixel_ratio": secrets.choice([1.0, 1.25, 1.5, 2.0]),
        "max_touch_points": 0,
        "platform": "Win32",
        "canvas_noise": secrets.token_hex(4),
    }


def _build_fingerprint_script(fp_config):
    """Build the JS snippet injected into the browser to override fingerprinting surfaces."""
    return """() => {
        const config = """ + json.dumps(fp_config) + """;

        // Navigator overrides
        Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => config.hardware_concurrency});
        Object.defineProperty(navigator, 'deviceMemory', {get: () => config.device_memory});
        Object.defineProperty(navigator, 'maxTouchPoints', {get: () => config.max_touch_points});
        Object.defineProperty(navigator, 'platform', {get: () => config.platform});
        Object.defineProperty(navigator, 'languages', {get: () => [config.locale, config.locale.split('-')[0]]});

        // Screen overrides
        Object.defineProperty(screen, 'width', {get: () => config.screen.width});
        Object.defineProperty(screen, 'height', {get: () => config.screen.height});
        Object.defineProperty(screen, 'availWidth', {get: () => config.screen.width});
        Object.defineProperty(screen, 'availHeight', {get: () => config.screen.height - 40});
        Object.defineProperty(screen, 'colorDepth', {get: () => config.color_depth});
        Object.defineProperty(screen, 'pixelDepth', {get: () => config.color_depth});
        Object.defineProperty(window, 'devicePixelRatio', {get: () => config.pixel_ratio});

        // WebGL fingerprint
        const getParameterOrig = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(param) {
            if (param === 37445) return config.webgl_vendor;
            if (param === 37446) return config.webgl_renderer;
            return getParameterOrig.call(this, param);
        };
        const getParameterOrig2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(param) {
            if (param === 37445) return config.webgl_vendor;
            if (param === 37446) return config.webgl_renderer;
            return getParameterOrig2.call(this, param);
        };

        // Canvas fingerprint noise
        const toDataURLOrig = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {
            const ctx = this.getContext('2d');
            if (ctx) {
                const noise = parseInt(config.canvas_noise, 16);
                const imageData = ctx.getImageData(0, 0, Math.min(this.width, 2), Math.min(this.height, 2));
                for (let i = 0; i < imageData.data.length; i += 4) {
                    imageData.data[i] = (imageData.data[i] + (noise >> (i % 8)) % 3) & 0xFF;
                }
                ctx.putImageData(imageData, 0, 0);
            }
            return toDataURLOrig.call(this, type);
        };

        // AudioContext fingerprint
        const origGetChannelData = AudioBuffer.prototype.getChannelData;
        AudioBuffer.prototype.getChannelData = function(channel) {
            const data = origGetChannelData.call(this, channel);
            if (data.length > 0) {
                const noise = parseInt(config.canvas_noise.slice(0, 4), 16) / 65536;
                for (let i = 0; i < Math.min(data.length, 10); i++) {
                    data[i] += (noise * 0.0000001);
                }
            }
            return data;
        };

        // ClientRects noise
        const origGetBoundingClientRect = Element.prototype.getBoundingClientRect;
        Element.prototype.getBoundingClientRect = function() {
            const rect = origGetBoundingClientRect.call(this);
            const noise = parseInt(config.canvas_noise.slice(4, 8), 16) % 3 * 0.00001;
            return new DOMRect(rect.x + noise, rect.y + noise, rect.width + noise, rect.height + noise);
        };

        // Permissions API
        const origQuery = navigator.permissions.query;
        navigator.permissions.query = function(desc) {
            if (desc.name === 'notifications') {
                return Promise.resolve({state: 'prompt', onchange: null});
            }
            return origQuery.call(this, desc);
        };

        // Connection API
        if (navigator.connection) {
            Object.defineProperty(navigator.connection, 'rtt', {get: () => [50, 100, 150][Math.floor(Math.random() * 3)]});
            Object.defineProperty(navigator.connection, 'downlink', {get: () => [1.5, 2.5, 5, 10][Math.floor(Math.random() * 4)]});
        }

        // Battery API - return realistic-looking battery state
        if (!navigator.getBattery) {
            navigator.getBattery = () => Promise.resolve({
                charging: Math.random() > 0.3,
                chargingTime: Math.random() > 0.5 ? Infinity : Math.floor(Math.random() * 7200),
                dischargingTime: Math.floor(Math.random() * 20000) + 3600,
                level: 0.3 + Math.random() * 0.65,
                addEventListener: () => {},
            });
        }

        // Performance.now() - tiny noise to defeat precision-timing fingerprinting
        const perfNowOrig = Performance.prototype.now;
        Performance.prototype.now = function() {
            return perfNowOrig.call(this) + Math.random() * 0.1;
        };

        // Date.now() - tiny jitter
        const dateNowOrig = Date.now;
        Date.now = function() {
            return dateNowOrig() + Math.floor(Math.random() * 2);
        };

        // Hide the webdriver flag
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        delete navigator.__proto__.webdriver;

        // Simulate a minimal chrome runtime
        if (!window.chrome) {
            window.chrome = {runtime: {}, loadTimes: () => ({}), csi: () => ({})};
        }

        // Fake plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const arr = [
                    {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
                    {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: ''},
                    {name: 'Native Client', filename: 'internal-nacl-plugin', description: ''},
                ];
                arr.item = (i) => arr[i];
                arr.namedItem = (n) => arr.find(p => p.name === n);
                arr.refresh = () => {};
                return arr;
            }
        });

        // Fake mime types
        Object.defineProperty(navigator, 'mimeTypes', {
            get: () => {
                const arr = [
                    {type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format'},
                    {type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format'},
                ];
                arr.item = (i) => arr[i];
                arr.namedItem = (n) => arr.find(m => m.type === n);
                return arr;
            }
        });
    }"""


def inject_machine_ids(log=print):
    """Inject randomised machine identifiers into Kiro's storage.json."""
    storage_path = Path(os.environ.get("APPDATA", "")) / "Kiro" / "User" / "globalStorage" / "storage.json"
    if not storage_path.exists():
        log(f"storage.json not found at {storage_path}; skipping machine-id injection", "warn")
        return None

    ids = _random_machine_ids()
    try:
        storage = json.loads(storage_path.read_text(encoding="utf-8"))
        for key, value in ids.items():
            storage[key] = value
        storage_path.write_text(json.dumps(storage, indent=4), encoding="utf-8")
        log(f"Machine IDs injected into storage.json: machineId={ids['telemetry.machineId'][:16]}..., devDeviceId={ids['telemetry.devDeviceId'][:16]}...", "ok")
        return ids
    except Exception as e:
        log(f"Machine-id injection failed during storage.json write: {e}", "err")
        return None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _sha1_hash(value: str) -> str:
    return hashlib.sha1(value.encode()).hexdigest()


import random as _random

async def _human_type(page, locator, text, min_delay=35, max_delay=120):
    """Simulate human typing: char-by-char entry with randomised delays and occasional pauses."""
    await locator.click()
    await asyncio.sleep(_random.uniform(0.2, 0.5))
    await locator.fill("")
    for i, ch in enumerate(text):
        await page.keyboard.type(ch, delay=0)
        delay = _random.uniform(min_delay, max_delay) / 1000
        # Every few characters insert a longer pause (thinking / glancing at keyboard).
        if i > 0 and _random.random() < 0.08:
            delay += _random.uniform(0.2, 0.6)
        await asyncio.sleep(delay)
    await asyncio.sleep(_random.uniform(0.3, 0.8))


async def _human_delay(min_s=1.0, max_s=3.0):
    """Random sleep to approximate human reaction time."""
    await asyncio.sleep(_random.uniform(min_s, max_s))


async def _move_to_element(page, locator):
    """Move the mouse onto the target element in a vaguely-human way."""
    try:
        box = await locator.bounding_box()
        if box:
            target_x = box["x"] + box["width"] * _random.uniform(0.3, 0.7)
            target_y = box["y"] + box["height"] * _random.uniform(0.3, 0.7)
            await page.mouse.move(target_x, target_y, steps=_random.randint(5, 15))
            await asyncio.sleep(_random.uniform(0.1, 0.3))
    except Exception:
        pass


def _generate_password(length=16):
    upper = string.ascii_uppercase
    lower = string.ascii_lowercase
    digits = string.digits
    special = "!@#$%&*"
    required = [
        secrets.choice(upper), secrets.choice(upper),
        secrets.choice(lower), secrets.choice(lower),
        secrets.choice(digits), secrets.choice(digits),
        secrets.choice(special), secrets.choice(special),
    ]
    pool = upper + lower + digits + special
    rest = [secrets.choice(pool) for _ in range(length - len(required))]
    chars = required + rest
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


def _generate_name():
    return f"{secrets.choice(_FIRST_NAMES)} {secrets.choice(_LAST_NAMES)}"


class ShiroMailClient:
    """Compatibility shim delegating to the mail_providers module."""
    def __init__(self, base_url=None, api_key=None, domain_id=None):
        from mail_providers import ShiroMailProvider
        self._provider = ShiroMailProvider(
            base_url=base_url or SHIROMAIL_BASE,
            api_key=api_key or SHIROMAIL_KEY,
            domain_id=domain_id or SHIROMAIL_DOMAIN_ID,
        )

    def create_mailbox(self) -> str:
        addr = self._provider.create_mailbox()
        self.address = self._provider.address
        return addr

    def wait_otp(self, timeout=120, poll_interval=3) -> str:
        return self._provider.wait_otp(timeout=timeout, poll_interval=poll_interval)


def persist_tokens(client_id, client_secret, access_token, refresh_token, expires_in, log=print, email=None):
    cache_dir = Path.home() / ".aws" / "sso" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    client_id_hash = _sha1_hash(client_id)
    expires_at_str = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    token_data = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at_str,
        "clientIdHash": client_id_hash,
        "clientId": client_id,
        "authMethod": "IdC",
        "provider": "BuilderId",
        "region": "us-east-1",
    }
    if email:
        token_data["email"] = email
    token_path = cache_dir / "kiro-auth-token.json"
    token_path.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
    try:
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    log(f"OAuth token written to {token_path}", "ok")
    client_data = {
        "clientId": client_id,
        "clientSecret": client_secret,
        "expiresAt": (datetime.now(timezone.utc) + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }
    client_path = cache_dir / f"{client_id_hash}.json"
    client_path.write_text(json.dumps(client_data, indent=2), encoding="utf-8")
    try:
        os.chmod(client_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    log(f"OIDC client credentials saved to {client_path}", "ok")


def skip_onboarding(log=print):
    storage_path = Path(os.environ.get("APPDATA", "")) / "Kiro" / "User" / "globalStorage" / "storage.json"
    if not storage_path.exists():
        return False
    try:
        storage = json.loads(storage_path.read_text(encoding="utf-8"))
        storage["kiroAgent.onboarding.onboardingCompleted"] = "true"
        storage_path.write_text(json.dumps(storage, indent=2), encoding="utf-8")
        log(f"Onboarding flag set in storage.json: onboardingCompleted=true", "ok")
        return True
    except Exception as e:
        log(f"Failed to update onboarding flag in storage.json: {e}", "err")
        return False


async def _dismiss_cookie(page):
    for sel in [
        'xpath=//button[@data-id="awsccc-cb-btn-accept"]',
        'xpath=//div[contains(@class,"awsccc")]//button[contains(text(),"Accept")]',
        'xpath=//button[text()="Accept"]',
    ]:
        loc = page.locator(sel)
        try:
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.click()
                await asyncio.sleep(1)
                return True
        except Exception:
            pass
    try:
        await page.evaluate("""() => {
            const el = document.querySelector('[id*="awsccc"], [class*="awsccc"]');
            if (el) el.remove();
        }""")
    except Exception:
        pass
    return False


async def _click_submit(page, label_contains=None, timeout=10000):
    if label_contains:
        btn = page.locator(f'xpath=//form//button[@type="submit"][contains(text(),"{label_contains}")]')
    else:
        btn = page.locator('xpath=//form//button[@type="submit"]')
    for i in range(await btn.count()):
        b = btn.nth(i)
        if await b.is_visible():
            await b.scroll_into_view_if_needed()
            await asyncio.sleep(0.3)
            await b.click(timeout=timeout)
            return True
    return False


def _parse_proxy_url(url):
    """Parse a proxy URL like http://user:pass@host:port into a Playwright proxy dict.

    Returns None if the URL is empty / malformed. The returned dict includes a
    `bypass` string that keeps localhost traffic direct so the local OAuth
    callback server (127.0.0.1:3128) doesn't get routed through the proxy.
    """
    if not url:
        return None
    try:
        p = urlparse(url.strip())
    except Exception:
        return None
    if not p.hostname or not p.port:
        return None
    scheme = p.scheme or "http"
    server = f"{scheme}://{p.hostname}:{p.port}"
    out = {
        "server": server,
        # Keep the local callback server direct — proxies can't forward to the
        # client's own 127.0.0.1 and return HTTP 400 when they try.
        "bypass": "127.0.0.1,localhost,*.local",
    }
    if p.username:
        out["username"] = p.username
    if p.password:
        out["password"] = p.password
    return out


async def register(headless=True, auto_login=True, skip_onboard=True,
                   mail_url=None, mail_key=None, mail_domain_id=None,
                   mail_provider_instance=None,
                   proxy_url=None,
                   log=print, cancel_check=None):
    """
    Run the full Kiro auto-registration flow.

    Args:
        headless: run the browser headlessly
        auto_login: whether to inject local tokens at the end
        skip_onboard: whether to skip the onboarding flow
        mail_url: mail provider API base URL
        mail_key: API key (ShiroMail requires one)
        mail_domain_id: domain ID (ShiroMail requires one)
        mail_provider_instance: pre-built MailProvider instance (preferred when supplied)
        proxy_url: optional HTTP/SOCKS proxy URL (http://user:pass@host:port).
                   When set, all outbound traffic (curl_cffi + Playwright) routes through it.
        log: logging callback; called as log(msg, level)
        cancel_check: callable that returns True to abort
    Returns:
        dict with account info, or None.
    """
    from curl_cffi import requests as curl_requests
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    if cancel_check and cancel_check():
        return None

    # Build a random fingerprint config.
    fp_config = _random_fingerprint_config()
    log(f"Generated browser fingerprint: Chrome/{fp_config['user_agent'].split('Chrome/')[1].split(' ')[0]}, "
        f"viewport={fp_config['viewport']['width']}x{fp_config['viewport']['height']}, "
        f"timezone={fp_config['timezone']}", "dbg")

    pw_proxy = _parse_proxy_url(proxy_url)
    if pw_proxy:
        log(f"Residential proxy configured: {pw_proxy['server']}", "info")

    if proxy_url:
        s = curl_requests.Session(
            impersonate="chrome131",
            proxies={"http": proxy_url, "https": proxy_url},
        )
    else:
        s = curl_requests.Session(impersonate="chrome131")
    if mail_provider_instance:
        mail = mail_provider_instance
    else:
        mail = ShiroMailClient(base_url=mail_url, api_key=mail_key, domain_id=mail_domain_id)
    email = mail.create_mailbox()
    password = _generate_password()
    full_name = _generate_name()
    log(f"Generated credentials - Email: {email}", "info")
    log(f"Generated credentials - Password: {password[:4]}****", "dbg")
    log(f"Generated credentials - Name: {full_name}", "dbg")

    def _partial_result(reason="unknown"):
        """Return a partial record when registration fails mid-way so callers can still persist it."""
        return {
            "email": email,
            "password": password,
            "full_name": full_name,
            "provider": "BuilderId",
            "authMethod": "IdC",
            "region": "us-east-1",
            "accessToken": "",
            "refreshToken": "",
            "incomplete": True,
            "failReason": reason,
        }

    # Phase 1: OIDC client registration
    log("Phase 1: Starting OIDC client registration with AWS", "info")
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _b64url(hashlib.sha256(code_verifier.encode()).digest())
    state_val = secrets.token_urlsafe(32)

    reg_resp = s.post(f"{REG_OIDC}/client/register", json={
        "clientName": "Kiro IDE", "clientType": "public",
        "grantTypes": ["authorization_code", "refresh_token"],
        "issuerUrl": ISSUER_URL,
        "redirectUris": [REG_REDIRECT_URI], "scopes": REG_SCOPES,
    }, timeout=25, verify=False)
    reg = reg_resp.json()
    if "clientId" not in reg:
        log(f"OIDC client registration failed - response: {reg}", "err")
        return _partial_result("OIDC registration failed")
    client_id = reg["clientId"]
    client_secret = reg["clientSecret"]
    log(f"OIDC client registered successfully - clientId: {client_id[:16]}...", "ok")

    signin_url = f"{KIRO_SIGNIN_URL}?" + urlencode({
        "state": state_val,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "redirect_uri": REG_REDIRECT_URI,
        "redirect_from": "KiroIDE",
    })

    # Phase 2: local callback server + Playwright
    log(f"Phase 2: Launching Playwright browser for registration flow (headless={headless})", "info")
    authorization_code = ""

    class CallbackHandler(BaseHTTPRequestHandler):
        signin_callback_params = {}

        def do_GET(self_h):
            nonlocal authorization_code
            parsed = urlparse(self_h.path)
            qs = parse_qs(parsed.query)
            code = qs.get("code", [""])[0]
            if code:
                authorization_code = code
                log(f"OAuth authorization code received from callback server", "ok")
                self_h.send_response(200)
                self_h.send_header("Content-Type", "text/html")
                self_h.end_headers()
                self_h.wfile.write(b"<html><body><h2>Registration complete!</h2></body></html>")
            elif "signin/callback" in parsed.path or qs.get("login_option"):
                CallbackHandler.signin_callback_params = {k: v[0] for k, v in qs.items()}
                log(f"AWS signin callback received from redirect chain", "ok")
                self_h.send_response(200)
                self_h.send_header("Content-Type", "text/html")
                self_h.end_headers()
                self_h.wfile.write(b"<html><body><p>Redirecting...</p></body></html>")
            else:
                self_h.send_response(200)
                self_h.send_header("Content-Type", "text/html")
                self_h.end_headers()
                self_h.wfile.write(b"<html><body><p>OK</p></body></html>")

        def log_message(self_h, *args):
            pass

    # Ensure port 3128 is free before we bind the callback server.
    # The fast path is a simple probe bind; on EADDRINUSE we attempt to
    # reclaim the port by killing the stale owner. Windows uses netstat
    # + taskkill; POSIX (macOS / Linux) uses lsof + kill.
    def _free_port_3128():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", 3128))
            sock.close()
            return True
        except OSError:
            sock.close()
        import subprocess
        killed = False
        try:
            if os.name == "nt":
                r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
                for line in r.stdout.splitlines():
                    if ":3128" in line and "LISTENING" in line:
                        pid = line.strip().split()[-1]
                        if pid.isdigit() and int(pid) != os.getpid():
                            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
                            killed = True
            else:
                # macOS / Linux: lsof -t prints PIDs; signal each one that isn't us.
                r = subprocess.run(
                    ["lsof", "-tiTCP:3128", "-sTCP:LISTEN"],
                    capture_output=True, text=True,
                )
                for pid_str in r.stdout.split():
                    try:
                        pid = int(pid_str)
                    except ValueError:
                        continue
                    if pid != os.getpid():
                        try:
                            os.kill(pid, 15)  # SIGTERM first
                        except Exception:
                            pass
                        killed = True
                if killed:
                    # Give the stale process a moment to release; escalate if it's stubborn.
                    import time as _t
                    _t.sleep(0.8)
                    r2 = subprocess.run(
                        ["lsof", "-tiTCP:3128", "-sTCP:LISTEN"],
                        capture_output=True, text=True,
                    )
                    for pid_str in r2.stdout.split():
                        try:
                            pid = int(pid_str)
                        except ValueError:
                            continue
                        if pid != os.getpid():
                            try:
                                os.kill(pid, 9)  # SIGKILL
                            except Exception:
                                pass
        except Exception:
            pass
        if killed:
            await_sleep_hint = True
        else:
            await_sleep_hint = False
        return await_sleep_hint  # tells caller whether we need to wait & re-probe

    needs_wait = _free_port_3128()
    if needs_wait:
        await asyncio.sleep(1.2)

    try:
        callback_server = HTTPServer(("127.0.0.1", 3128), CallbackHandler)
    except OSError as e:
        log(f"Could not bind 127.0.0.1:3128 ({e}). Another process still holds it - "
            "try `lsof -tiTCP:3128 -sTCP:LISTEN | xargs kill -9` and retry.", "err")
        return _partial_result("port 3128 busy")
    callback_server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv_thread = threading.Thread(target=callback_server.serve_forever, daemon=True)
    srv_thread.start()
    log("Local callback server listening on 127.0.0.1:3128", "ok")

    try:
        async with async_playwright() as p:
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-first-run",
                f"--window-size={fp_config['screen']['width']},{fp_config['screen']['height']}",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ]
            if headless:
                launch_args += ["--disable-gpu", "--no-sandbox",
                                "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            launch_kwargs = {"headless": headless, "args": launch_args}
            if pw_proxy:
                launch_kwargs["proxy"] = pw_proxy
            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                viewport=fp_config["viewport"],
                screen=fp_config["screen"],
                locale=fp_config["locale"],
                timezone_id=fp_config["timezone"],
                user_agent=fp_config["user_agent"],
                color_scheme="light",
                device_scale_factor=fp_config["pixel_ratio"],
            )
            page = await context.new_page()
            page.set_default_timeout(90000)  # 90s timeout for all operations
            await Stealth().apply_stealth_async(page)

            # Track the latest create-identity outcome so the OTP retry loop can
            # short-circuit on fatal errors (OTP consumed, account banned, etc).
            create_identity_status = {"code": None, "body": ""}

            # Intercept profile.aws API responses for debugging visibility.
            async def _on_profile_response(response):
                url = response.url
                if "profile.aws" in url and "/api/" in url:
                    try:
                        body = await response.text()
                        endpoint = url.split("/api/")[-1]
                        log(f"[API] {endpoint} -> {response.status} {body[:150]}", "dbg")
                        if "create-identity" in endpoint:
                            err_code = ""
                            try:
                                import json as _j
                                parsed = _j.loads(body)
                                err_code = parsed.get("errorCode", "")
                            except Exception:
                                pass
                            create_identity_status["code"] = err_code
                            create_identity_status["body"] = body
                    except Exception:
                        pass
            page.on("response", _on_profile_response)

            # Inject the deep fingerprint overrides.
            await context.add_init_script(_build_fingerprint_script(fp_config))

            await page.goto(signin_url, timeout=60000)
            # networkidle can never fire on ad/analytics-heavy pages; prefer
            # domcontentloaded and fall back to a fixed settle window when
            # even that stalls (common over a residential proxy).
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
            except Exception:
                pass
            await asyncio.sleep(3)
            await _dismiss_cookie(page)

            # Click the AWS Builder ID button.
            if "app.kiro.dev" in page.url:
                log("Selecting sign-in method...")
                await asyncio.sleep(2)
                signin_clicked = False
                for sel in [
                    'xpath=//*[@id="layout-viewport"]/div/div/div/div[2]/div/div[1]/button[3]',
                    'xpath=//button[contains(text(),"AWS Builder ID")]',
                    'xpath=//button[contains(text(),"Builder ID")]',
                    'xpath=//button[contains(text(),"Sign in")]',
                    'xpath=//button[contains(text(),"Continue")]',
                ]:
                    loc = page.locator(sel)
                    try:
                        if await loc.count() > 0 and await loc.first.is_visible():
                            await loc.first.click()
                            signin_clicked = True
                            log("Clicked sign-in button", "ok")
                            break
                    except Exception:
                        pass

                if signin_clicked:
                    await asyncio.sleep(3)
                    if not CallbackHandler.signin_callback_params:
                        try:
                            await page.evaluate("""() => {
                                const btn = document.querySelector('#layout-viewport button:nth-child(3)') ||
                                            document.querySelectorAll('#layout-viewport button')[2];
                                if (btn) btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                            }""")
                        except Exception:
                            pass
                        await asyncio.sleep(3)

                for _ in range(20):
                    if CallbackHandler.signin_callback_params:
                        break
                    await asyncio.sleep(1)

            # Build the OIDC authorize URL.
            if CallbackHandler.signin_callback_params and not authorization_code:
                log("Redirecting to the authorization page...")
                authorize_url = f"{REG_OIDC}/authorize?" + urlencode({
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": REG_REDIRECT_URI,
                    "scopes": ",".join(REG_SCOPES),
                    "state": state_val,
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                })
                await page.goto(authorize_url, timeout=60000)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=30000)
                except Exception:
                    pass
                await asyncio.sleep(3)

            # Wait until we land on signin.aws or profile.aws.
            for _ in range(10):
                if "signin.aws" in page.url or "profile.aws" in page.url:
                    break
                await asyncio.sleep(2)
            await asyncio.sleep(2)
            log("Arrived at the registration page", "ok")

            # On signin.aws, fill the email.
            if "signin.aws" in page.url:
                email_input = page.locator('xpath=//input[@type="email"]')
                if await email_input.count() == 0:
                    email_input = page.locator('xpath=//input[@type="text"]').first
                await _move_to_element(page, email_input)
                await _human_type(page, email_input, email)
                await _human_delay(0.8, 1.5)
                log(f"Email filled: {email}")
                await page.evaluate("""() => {
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const visible = buttons.filter(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                    for (const b of visible) {
                        const t = (b.innerText || '').toLowerCase();
                        if (t.includes('continue') || t.includes('next') || t.includes('submit') || t.includes('verify')) {
                            b.click(); return;
                        }
                    }
                    if (visible.length > 0) visible[visible.length - 1].click();
                }""")
                await _human_delay(2, 4)

            # Wait for profile.aws.
            for _ in range(15):
                if "profile.aws" in page.url:
                    break
                await asyncio.sleep(2)
            await asyncio.sleep(2)

            if "profile.aws" not in page.url:
                log(f"Failed to reach profile.aws registration page after navigation (current URL: {page.url})", "err")
                await browser.close()
                callback_server.shutdown()
                return _partial_result("did not reach registration page")

            # Warm-up behaviour: simulate a human browsing the page so TES sees normal interactions.
            try:
                vp = page.viewport_size
                for _ in range(3):
                    await page.mouse.move(
                        _random.randint(100, vp["width"] - 100),
                        _random.randint(100, vp["height"] - 100),
                        steps=_random.randint(10, 25)
                    )
                    await asyncio.sleep(_random.uniform(0.3, 0.8))
                await page.mouse.wheel(0, _random.randint(50, 150))
                await asyncio.sleep(_random.uniform(0.5, 1.0))
                await page.mouse.wheel(0, -_random.randint(30, 80))
                await asyncio.sleep(_random.uniform(0.3, 0.6))
            except Exception:
                pass

            # --- State machine ----------------------------------------------
            async def detect_state():
                if authorization_code:
                    return "DONE"
                url = page.url
                if "127.0.0.1:3128" in url or "localhost:3128" in url:
                    return "CALLBACK"
                try:
                    result = await page.evaluate("""() => {
                        const url = location.href;
                        const pwds = document.querySelectorAll('input[type="password"]');
                        const visiblePwds = Array.from(pwds).filter(e => e.offsetWidth > 0);
                        const nameInput = document.querySelector('input[placeholder*="Silva"]');
                        const otpInput = document.querySelector('input[inputmode="numeric"]') ||
                                         document.querySelector('input[autocomplete="one-time-code"]') ||
                                         document.querySelector('input[name*="otp"]') ||
                                         document.querySelector('input[name*="code"]') ||
                                         document.querySelector('input[placeholder*="6-digit"]') ||
                                         document.querySelector('input[placeholder*="digit"]');
                        const emailInput = document.querySelector('input[type="email"]');
                        const urlHasOtp = url.includes('verify-otp') || url.includes('otp');
                        // Is this an authorisation-consent page (with allow/authorize buttons)?
                        const buttons = Array.from(document.querySelectorAll('button'));
                        const visibleBtns = buttons.filter(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                        const hasConsentBtn = visibleBtns.some(b => {
                            const t = (b.innerText || '').toLowerCase();
                            return t.includes('allow') || t.includes('authorize') ||
                                   t.includes('accept') || t.includes('confirm');
                        });
                        // Is the page still loading (no interactive elements)?
                        const hasAnyInput = document.querySelectorAll('input:not([type="hidden"])').length > 0;
                        const hasAnyButton = visibleBtns.length > 0;
                        const isLoading = !hasAnyInput && !hasAnyButton;
                        return {
                            url, visiblePwdCount: visiblePwds.length,
                            hasName: !!(nameInput && nameInput.offsetWidth > 0),
                            hasOtp: !!(otpInput && otpInput.offsetWidth > 0),
                            hasEmail: !!(emailInput && emailInput.offsetWidth > 0),
                            urlHasOtp, hasConsentBtn, isLoading,
                        };
                    }""")
                except Exception:
                    return "UNKNOWN"
                if "chrome-error" in result["url"]:
                    return "CALLBACK"
                if result["visiblePwdCount"] >= 1:
                    return "PASSWORD"
                if result["hasOtp"] or (result["urlHasOtp"] and not result["isLoading"]):
                    return "OTP"
                if result["hasName"]:
                    return "NAME"
                if result["hasEmail"]:
                    return "EMAIL"
                if "awsapps.com" in result["url"] and result["hasConsentBtn"]:
                    return "CONSENT"
                if "profile.aws" in result["url"]:
                    return "LOADING"
                if result["isLoading"]:
                    return "LOADING"
                return "UNKNOWN"

            async def wait_for_state(target_states, timeout=60):
                deadline = time.time() + timeout
                while time.time() < deadline:
                    if cancel_check and cancel_check():
                        return "CANCELLED"
                    st = await detect_state()
                    if st in target_states or st == "DONE":
                        return st
                    if st == "CALLBACK":
                        return st
                    await asyncio.sleep(1.5)
                return await detect_state()

            # Phase 3: name entry
            log("Phase 3: filling the registration form")
            await asyncio.sleep(2)
            await _dismiss_cookie(page)

            state = await wait_for_state(["EMAIL", "NAME", "OTP", "PASSWORD", "CONSENT", "DONE"], timeout=30)
            if state == "CANCELLED":
                await browser.close()
                callback_server.shutdown()
                return _partial_result("user cancelled")

            # EMAIL (fallback for when signin.aws email entry didn't stick).
            if state == "EMAIL":
                email_input = page.locator('xpath=//input[@type="email"]')
                if await email_input.count() == 0:
                    email_input = page.locator('xpath=//input[@type="text"]')
                if await email_input.count() > 0:
                    await _move_to_element(page, email_input.first)
                    await _human_type(page, email_input.first, email)
                    await _human_delay(0.5, 1.0)
                    log(f"Email filled (state machine fallback): {email}", "ok")
                    await page.evaluate("""() => {
                        const buttons = Array.from(document.querySelectorAll('button'));
                        const visible = buttons.filter(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                        for (const b of visible) {
                            const t = (b.innerText || '').toLowerCase();
                            if (t.includes('continue') || t.includes('next') || t.includes('submit') || t.includes('verify')) {
                                b.click(); return;
                            }
                        }
                        if (visible.length > 0) visible[visible.length - 1].click();
                    }""")
                    await _human_delay(3, 5)
                state = await wait_for_state(["NAME", "OTP", "PASSWORD", "CONSENT", "DONE"], timeout=30)
                if state == "CANCELLED":
                    await browser.close()
                    callback_server.shutdown()
                    return _partial_result("user cancelled")

            if state == "NAME":
                name_field = page.locator('xpath=//input[contains(@placeholder,"Silva")]')
                for attempt in range(3):
                    try:
                        await _move_to_element(page, name_field.first)
                        await _human_type(page, name_field.first, full_name)
                        await _human_delay(0.5, 1.0)
                        filled_val = await name_field.first.input_value()
                        if filled_val == full_name:
                            log(f"Name filled: '{full_name}'", "ok")
                            break
                    except Exception:
                        await asyncio.sleep(1)
                for attempt in range(3):
                    clicked = False
                    try:
                        await page.evaluate("""() => {
                            const buttons = Array.from(document.querySelectorAll('button'));
                            const visible = buttons.filter(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                            for (const b of visible) {
                                const t = (b.innerText || '').toLowerCase();
                                if (t.includes('continue') || t.includes('next') || t.includes('submit') || t.includes('verify')) {
                                    b.click(); return;
                                }
                            }
                            if (visible.length > 0) visible[visible.length - 1].click();
                        }""")
                        clicked = True
                    except Exception:
                        pass
                    if not clicked:
                        try:
                            await page.keyboard.press("Enter")
                        except Exception:
                            pass
                    await asyncio.sleep(4)
                    new_state = await detect_state()
                    if new_state != "NAME":
                        log("Name submitted", "ok")
                        break
                state = await detect_state()

            # Phase 4: OTP verification
            if state not in ["DONE", "PASSWORD", "CONSENT", "CALLBACK"]:
                state = await wait_for_state(["OTP", "PASSWORD", "CONSENT", "DONE"], timeout=30)

            if state == "CANCELLED":
                await browser.close()
                callback_server.shutdown()
                return _partial_result("user cancelled")
            if state == "OTP":
                log("Phase 4: OTP verification")
                # Give the profile.aws React view time to finish rendering.
                await asyncio.sleep(3)
                otp_selectors = [
                    'xpath=//input[@inputmode="numeric"]',
                    'xpath=//input[@autocomplete="one-time-code"]',
                    'xpath=//input[contains(@placeholder,"6-digit") or contains(@placeholder,"digit")]',
                    'xpath=//input[contains(@name,"otp") or contains(@name,"code") or contains(@name,"verif")]',
                    'xpath=//input[contains(@id,"otp") or contains(@id,"code") or contains(@id,"verif")]',
                    'xpath=//input[contains(@placeholder,"code") or contains(@placeholder,"Code")]',
                    'xpath=//input[contains(@aria-label,"code") or contains(@aria-label,"verif")]',
                    'xpath=//input[contains(@class,"verification") or contains(@class,"otp")]',
                    'css=input[data-testid*="code"]',
                    'css=input[data-testid*="otp"]',
                    'css=input[data-testid*="verif"]',
                ]
                otp_input = None
                # Retry up to 3 times with a 2-second gap to absorb React render delays.
                for retry in range(3):
                    for sel in otp_selectors:
                        loc = page.locator(sel)
                        if await loc.count() > 0 and await loc.first.is_visible():
                            otp_input = loc.first
                            break
                    if otp_input:
                        break
                    # Fallback: pick the single visible text/tel/number input on the page.
                    all_inp = page.locator('xpath=//input[not(@type="hidden") and not(@type="password") and not(@type="email")]')
                    for i in range(await all_inp.count()):
                        inp = all_inp.nth(i)
                        if await inp.is_visible():
                            inp_type = await inp.get_attribute("type") or "text"
                            if inp_type in ("text", "tel", "number", ""):
                                otp_input = inp
                                break
                    if otp_input:
                        break
                    if retry < 2:
                        log(f"OTP input not ready yet; retrying ({retry+1}/3)...")
                        await asyncio.sleep(2)

                if not otp_input:
                    # Debug: dump attributes of every input on the page.
                    debug_info = await page.evaluate("""() => {
                        const inputs = document.querySelectorAll('input');
                        return Array.from(inputs).map(el => ({
                            type: el.type, name: el.name, id: el.id,
                            placeholder: el.placeholder,
                            inputmode: el.inputMode,
                            autocomplete: el.autocomplete,
                            ariaLabel: el.getAttribute('aria-label'),
                            className: el.className.substring(0, 80),
                            dataTestId: el.getAttribute('data-testid'),
                            visible: el.offsetWidth > 0 && el.offsetHeight > 0,
                            tagPath: el.closest('[class]')?.className?.substring(0, 60) || ''
                        }));
                    }""")
                    log(f"OTP input not found! Dumping page inputs:", "err")
                    for info in debug_info:
                        log(f"  {info}", "err")
                    await browser.close()
                    callback_server.shutdown()
                    return _partial_result("OTP input not found")

                log(f"OTP input located; waiting for the code ({mail.__class__.__name__})...", "ok")
                otp_code = ""
                otp_deadline = time.time() + 90
                while time.time() < otp_deadline:
                    if cancel_check and cancel_check():
                        log("User cancelled", "err")
                        await browser.close()
                        callback_server.shutdown()
                        return _partial_result("user cancelled")
                    otp_code = mail.wait_otp(timeout=5, poll_interval=3)
                    if otp_code:
                        break
                if not otp_code:
                    log("OTP wait timed out!", "err")
                    await browser.close()
                    callback_server.shutdown()
                    return _partial_result("OTP timeout")

                log(f"OTP code received from mail provider: {otp_code}", "info")
                # Simulate a human switching back from the mail client by nudging the mouse first.
                await _human_delay(2, 4)
                try:
                    vp = page.viewport_size
                    await page.mouse.move(
                        vp["width"] * _random.uniform(0.3, 0.7),
                        vp["height"] * _random.uniform(0.3, 0.5),
                        steps=_random.randint(8, 20)
                    )
                    await asyncio.sleep(_random.uniform(0.3, 0.8))
                except Exception:
                    pass
                await _move_to_element(page, otp_input)
                await otp_input.click()
                await asyncio.sleep(_random.uniform(0.3, 0.6))
                # Type the OTP char by char; avoid fill() to sidestep React controlled-input issues.
                # Slower-than-typical inter-key delay so the cadence looks human (TES monitors timing).
                for ch in otp_code:
                    await page.keyboard.type(ch, delay=0)
                    await asyncio.sleep(_random.uniform(0.18, 0.42))
                await _human_delay(1.2, 2.2)

                # Submit the OTP; TES may block the first attempt, so retry with growing gaps and more human noise.
                otp_dead = False
                for attempt in range(5):
                    # Reset the response tracker for this attempt so we observe a fresh outcome.
                    create_identity_status["code"] = None
                    create_identity_status["body"] = ""
                    try:
                        await page.evaluate("""() => {
                            const buttons = Array.from(document.querySelectorAll('button'));
                            const visible = buttons.filter(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                            for (const b of visible) {
                                const t = (b.innerText || '').toLowerCase();
                                if (t.includes('continue') || t.includes('next') || t.includes('submit') || t.includes('verify')) {
                                    b.click(); return;
                                }
                            }
                            if (visible.length > 0) visible[visible.length - 1].click();
                        }""")
                    except Exception:
                        await page.keyboard.press("Enter")
                    log(f"OTP submitted ({attempt+1}/5)")
                    # Progressive back-off: TES needs more time to re-evaluate after the first intercept.
                    wait_time = 4 + attempt * 3
                    await asyncio.sleep(wait_time)
                    new_state = await detect_state()
                    if new_state != "OTP":
                        log("OTP verified", "ok")
                        state = new_state
                        break
                    # If the API came back with INVALID_OTP the OTP has been consumed/expired
                    # server-side; further retries cannot recover this account.
                    api_err = create_identity_status["code"] or ""
                    if api_err == "INVALID_OTP":
                        log("OTP rejected as INVALID_OTP - server consumed/expired the code, aborting this account", "err")
                        otp_dead = True
                        break
                    # Still on the OTP page: check for an error banner (TES intercept).
                    try:
                        error_text = await page.evaluate("""() => {
                            const alerts = document.querySelectorAll('[role="alert"], [class*="error"], [class*="Error"]');
                            for (const el of alerts) {
                                const t = el.innerText.trim();
                                if (t && t.length > 3) return t;
                            }
                            return '';
                        }""")
                        if error_text:
                            log(f"TES intercept ({attempt+1}/5), re-playing input...", "warn")
                            # Simulate a human reacting to the error: move, click, clear, retype.
                            await page.mouse.move(
                                _random.randint(200, 800), _random.randint(200, 500),
                                steps=_random.randint(8, 15)
                            )
                            await _human_delay(2.0, 4.0)
                            await _move_to_element(page, otp_input)
                            await otp_input.click()
                            await asyncio.sleep(_random.uniform(0.2, 0.4))
                            # Select all + delete (Ctrl+A, Backspace).
                            await page.keyboard.press("Control+a")
                            await asyncio.sleep(_random.uniform(0.1, 0.3))
                            await page.keyboard.press("Backspace")
                            await asyncio.sleep(_random.uniform(0.3, 0.6))
                            # Retype char by char.
                            for ch in otp_code:
                                await page.keyboard.type(ch, delay=0)
                                await asyncio.sleep(_random.uniform(0.22, 0.48))
                            await _human_delay(1.2, 2.2)
                    except Exception:
                        pass

                if otp_dead:
                    await browser.close()
                    callback_server.shutdown()
                    return _partial_result("OTP rejected (consumed/expired)")

            # Phase 5: password setup
            if state not in ["DONE", "CONSENT", "CALLBACK"]:
                if state in ["UNKNOWN", "LOADING", "OTP"]:
                    log(f"Waiting for page transition (current: {state})...", "info")
                    await asyncio.sleep(3)
                state = await wait_for_state(["PASSWORD", "CONSENT", "DONE", "CALLBACK"], timeout=30)
                if state == "CANCELLED":
                    await browser.close()
                    callback_server.shutdown()
                    return _partial_result("user cancelled")
                log(f"Entering state: {state}", "info")

            if state == "PASSWORD":
                log("Phase 5: setting password")
                await _human_delay(1.5, 3.0)
                for _wait in range(10):
                    count = await page.locator('xpath=//input[@type="password"]').count()
                    if count >= 2:
                        break
                    await asyncio.sleep(1)
                for attempt in range(3):
                    try:
                        await page.evaluate("""(pwd) => {
                            const inputs = Array.from(document.querySelectorAll('input[type="password"]'))
                                .filter(el => el.offsetWidth > 0);
                            function setVal(el, val) {
                                const ns = Object.getOwnPropertyDescriptor(
                                    window.HTMLInputElement.prototype, 'value').set;
                                ns.call(el, val);
                                el.dispatchEvent(new Event('input', {bubbles: true}));
                                el.dispatchEvent(new Event('change', {bubbles: true}));
                            }
                            inputs.forEach(el => setVal(el, pwd));
                        }""", password)
                        log("Password filled", "ok")
                        await asyncio.sleep(0.5)
                        await page.evaluate("""() => {
                            const buttons = Array.from(document.querySelectorAll('button'));
                            const visible = buttons.filter(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                            for (const b of visible) {
                                const t = (b.innerText || '').toLowerCase();
                                if (t.includes('continue') || t.includes('next') || t.includes('submit') || t.includes('verify')) {
                                    b.click(); return;
                                }
                            }
                            if (visible.length > 0) visible[visible.length - 1].click();
                        }""")
                    except Exception:
                        await asyncio.sleep(2)
                        continue
                    await asyncio.sleep(5)
                    new_state = await detect_state()
                    if new_state != "PASSWORD":
                        log("Password set", "ok")
                        state = new_state
                        break

            # Phase 6: authorisation consent
            if state not in ["DONE", "CALLBACK"]:
                state = await wait_for_state(["CONSENT", "DONE", "CALLBACK"], timeout=45)

            if state == "CANCELLED":
                await browser.close()
                callback_server.shutdown()
                return _partial_result("user cancelled")
            if state == "CONSENT":
                log("Phase 6: authorisation consent screen")
                await asyncio.sleep(3)
                for attempt in range(10):
                    try:
                        clicked = await page.evaluate("""() => {
                            const buttons = Array.from(document.querySelectorAll('button'));
                            const visible = buttons.filter(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                            for (const b of visible) {
                                const t = (b.innerText || '').toLowerCase();
                                if (t.includes('allow') || t.includes('authorize') || t.includes('accept') || t.includes('confirm')) {
                                    b.click(); return true;
                                }
                            }
                            if (visible.length > 0) { visible[visible.length - 1].click(); return true; }
                            return false;
                        }""")
                    except Exception:
                        log("Consent page navigated", "ok")
                        state = "CALLBACK"
                        break
                    if clicked:
                        log("Clicked the authorise button", "ok")
                        await asyncio.sleep(4)
                        try:
                            new_state = await detect_state()
                        except Exception:
                            state = "CALLBACK"
                            break
                        if new_state != "CONSENT":
                            state = new_state
                            break
                    await asyncio.sleep(2)

            # Wait for the OAuth callback code.
            log("Waiting for OAuth callback...")
            for i in range(30):
                if cancel_check and cancel_check():
                    log("User cancelled", "err")
                    await browser.close()
                    callback_server.shutdown()
                    return _partial_result("user cancelled")
                if authorization_code:
                    break
                current_url = page.url
                if "127.0.0.1:3128" in current_url or "localhost:3128" in current_url:
                    qs = parse_qs(urlparse(current_url).query)
                    authorization_code = qs.get("code", [""])[0]
                    if authorization_code:
                        break
                if "code=" in current_url and "code_challenge" not in current_url:
                    qs = parse_qs(urlparse(current_url).query)
                    code_val = qs.get("code", [""])[0]
                    if code_val and len(code_val) > 10:
                        authorization_code = code_val
                        break
                if "awsapps.com" in current_url:
                    try:
                        await page.evaluate("""() => {
                            const buttons = Array.from(document.querySelectorAll('button'));
                            const visible = buttons.filter(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                            for (const b of visible) {
                                const t = (b.innerText || '').toLowerCase();
                                if (t.includes('allow') || t.includes('authorize') || t.includes('accept') || t.includes('confirm')) {
                                    b.click(); return;
                                }
                            }
                            if (visible.length > 0) visible[visible.length - 1].click();
                        }""")
                    except Exception:
                        pass
                await asyncio.sleep(2)

            await browser.close()
    finally:
        # `shutdown` stops the serve_forever loop, `server_close` releases the
        # listening socket. Without server_close the socket stays bound until
        # the owning Python process exits -- which broke retry #2..5 in the
        # same GUI session.
        try:
            callback_server.shutdown()
        except Exception:
            pass
        try:
            callback_server.server_close()
        except Exception:
            pass

    # Phase 7: token exchange
    if not authorization_code:
        log("No authorization code obtained!", "err")
        return _partial_result("no authorization code")

    log("Authorization code obtained", "ok")
    log("Exchanging for tokens...")

    token_resp = s.post(f"{REG_OIDC}/token", json={
        "clientId": client_id,
        "clientSecret": client_secret,
        "grantType": "authorization_code",
        "code": authorization_code,
        "redirectUri": REG_REDIRECT_URI,
        "codeVerifier": code_verifier,
    }, timeout=25, verify=False)

    if token_resp.status_code != 200:
        log(f"OAuth token exchange failed with HTTP {token_resp.status_code} from {REG_OIDC}/token", "err")
        return _partial_result("token exchange failed")

    tokens = token_resp.json()
    access_token = tokens.get("accessToken", "")
    refresh_token = tokens.get("refreshToken", "")
    expires_in = tokens.get("expiresIn", 28800)

    if not access_token:
        log("Token exchange returned empty accessToken in response", "err")
        return _partial_result("missing accessToken")

    log(f"OAuth tokens obtained successfully (accessToken expires in {expires_in}s)", "ok")

    # Inject local tokens + random machine IDs.
    if auto_login:
        log("Injecting OAuth tokens and machine IDs into local Kiro storage", "info")
        persist_tokens(client_id, client_secret, access_token, refresh_token, expires_in, log, email=email)
        machine_ids = inject_machine_ids(log)
        if skip_onboard:
            skip_onboarding(log)

    log("=" * 40, "info")
    log("AWS Builder ID registration completed successfully!", "ok")
    log(f"  Account Email: {email}", "info")
    log(f"  Account Password: {password}", "info")
    log("=" * 40, "info")

    return {
        "email": email,
        "password": password,
        "full_name": full_name,
        "provider": "BuilderId",
        "authMethod": "IdC",
        "region": "us-east-1",
        "clientId": client_id,
        "clientSecret": client_secret,
        "clientIdHash": _sha1_hash(client_id),
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).strftime("%Y/%m/%d %H:%M:%S"),
    }


# ============================================================================
# 9router OAuth Device Code Flow Registration
# ============================================================================

async def register_via_9router_oauth(headless=True, auto_login=True, skip_onboard=True,
                                     mail_provider_instance=None,
                                     router9_config=None,
                                     proxy_url=None,
                                     log=print, cancel_check=None,
                                     cached_email=None,
                                     cached_device_code_data=None):
    """
    Register AWS Builder ID via 9router OAuth device code flow.

    Automatically exports account to 9router during registration instead of
    requiring a separate export step afterward.

    Args:
        headless: run browser headlessly
        auto_login: inject local tokens after registration
        skip_onboard: skip onboarding flow
        mail_provider_instance: MailProvider instance
        router9_config: dict with {base_url, password, auth_token, auth_token_expires_at}
        proxy_url: optional HTTP/SOCKS proxy
        log: logging callback
        cached_email: optional pre-generated email to reuse (avoids creating new mailbox)
        cached_device_code_data: optional dict with device code to reuse if not expired
            {device_code, verification_uri_complete, client_id, client_secret,
             code_verifier, region, user_code, expires_at_timestamp}
        cancel_check: callable returning True to abort

    Returns:
        dict with account info + router9_exported flag, or None
    """
    from curl_cffi import requests as curl_requests
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    from router9_oauth import Router9OAuthClient

    if cancel_check and cancel_check():
        return None

    if not router9_config:
        log("router9_config required for 9router OAuth device code registration flow", "err")
        return None

    # Setup
    fp_config = _random_fingerprint_config()
    log(f"Generated browser fingerprint for 9router flow: Chrome/{fp_config['user_agent'].split('Chrome/')[1].split(' ')[0]}, "
        f"viewport={fp_config['viewport']['width']}x{fp_config['viewport']['height']}", "dbg")

    pw_proxy = _parse_proxy_url(proxy_url)
    if pw_proxy:
        log(f"Proxy: {pw_proxy['server']}", "ok")

    if not mail_provider_instance:
        log("mail_provider_instance required for 9router OAuth registration flow", "err")
        return None

    mail = mail_provider_instance

    # Email cache reuse
    if cached_email:
        email = cached_email
        log(f"Reusing cached email: {email}", "dbg")
        # Reset mail provider state to allow polling new OTP
        if hasattr(mail, '_seen_uids'):
            mail._seen_uids = set()
            log("Reset mail provider seen UIDs for fresh OTP polling", "dbg")
        if hasattr(mail, '_created_at'):
            mail._created_at = time.time()
            log("Reset mail provider created timestamp", "dbg")
    else:
        email = mail.create_mailbox()
        log(f"Generated new email: {email}", "dbg")

    password = _generate_password()
    full_name = _generate_name()
    log(f"Email: {email}", "ok")

    def _partial(reason="unknown"):
        return {
            "email": email, "password": password, "full_name": full_name,
            "provider": "BuilderId", "authMethod": "IdC", "region": "us-east-1",
            "accessToken": "", "refreshToken": "", "incomplete": True,
            "failReason": reason, "router9_exported": False,
            "device_code_info": {
                "device_code": device_code if 'device_code' in locals() else "",
                "verification_uri_complete": verify_url if 'verify_url' in locals() else "",
                "client_id": client_id if 'client_id' in locals() else "",
                "client_secret": client_secret if 'client_secret' in locals() else "",
                "code_verifier": code_verifier if 'code_verifier' in locals() else "",
                "region": region,
                "user_code": user_code if 'user_code' in locals() else "",
                "expires_at_timestamp": device_code_expires_at if 'device_code_expires_at' in locals() else 0,
            } if 'device_code' in locals() else None,
        }

    # Phase 1: 9router device code
    log("Phase 1: 9router OAuth device code")
    r9 = Router9OAuthClient(router9_config["base_url"], router9_config["password"])

    ok, token, expires = r9.ensure_auth_token(
        router9_config.get("auth_token"),
        router9_config.get("auth_token_expires_at"),
        log
    )
    if not ok:
        return _partial("9router auth failed")

    router9_config["auth_token"] = token
    router9_config["auth_token_expires_at"] = expires
    r9.auth_token = token  # Ensure instance variable is set for poll_account()

    # Device code cache reuse
    if cached_device_code_data and time.time() < cached_device_code_data.get('expires_at_timestamp', 0):
        device_code = cached_device_code_data['device_code']
        verify_url = cached_device_code_data['verification_uri_complete']
        client_id = cached_device_code_data['client_id']
        client_secret = cached_device_code_data['client_secret']
        code_verifier = cached_device_code_data['code_verifier']
        region = cached_device_code_data.get('region', 'us-east-1')
        user_code = cached_device_code_data.get('user_code', '')
        device_code_expires_at = cached_device_code_data['expires_at_timestamp']
        remaining = int(device_code_expires_at - time.time())
        log(f"Reusing cached device code (user_code: {user_code}, expires in {remaining}s)", "dbg")
    else:
        dev = r9.get_device_code(log)
        if not dev["ok"]:
            return _partial("device code failed")

        device_code = dev["device_code"]
        verify_url = dev["verification_uri_complete"]
        client_id = dev["_clientId"]
        client_secret = dev["_clientSecret"]
        code_verifier = dev["codeVerifier"]
        region = dev.get("_region", "us-east-1")
        user_code = dev.get("user_code", "")
        device_code_expires_at = time.time() + 600  # 10 minutes from now
        log(f"Generated new device code (user_code: {user_code}, expires in 600s)", "dbg")

    log(f"Device code obtained - User code: {user_code}", "info")
    log(f"Device code obtained - Verification URL: {verify_url[:60]}...", "dbg")

    # Phase 2: Browser automation
    log(f"Phase 2: Launching browser for device code authorization (headless={headless})", "info")

    async with async_playwright() as p:
        args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ]
        if headless:
            args += ["--disable-gpu", "--no-sandbox"]

        kwargs = {"headless": headless, "args": args}
        if pw_proxy:
            kwargs["proxy"] = pw_proxy

        browser = await p.chromium.launch(**kwargs)
        ctx = await browser.new_context(
            viewport=fp_config["viewport"],
            screen=fp_config["screen"],
            locale=fp_config["locale"],
            timezone_id=fp_config["timezone"],
            user_agent=fp_config["user_agent"],
            device_scale_factor=fp_config["pixel_ratio"],
        )
        page = await ctx.new_page()
        await Stealth().apply_stealth_async(page)
        await ctx.add_init_script(_build_fingerprint_script(fp_config))

        try:
            await page.goto(verify_url, timeout=60000)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
            except:
                pass
            await asyncio.sleep(3)
            await _dismiss_cookie(page)

            # Wait for automatic redirect to signin page (if no session exists)
            log("Waiting for signup/signin page...", "info")
            for _ in range(15):
                current_url = page.url
                log(f"Current URL: {current_url}", "dbg")
                if "signin.aws" in current_url or "profile.aws" in current_url:
                    break
                await asyncio.sleep(2)

            log("At registration page", "ok")

            # Fill email if on signin page
            if "signin.aws" in page.url:
                log("Looking for email input and Continue button in parallel...", "info")

                # Helper function to find element by trying multiple selectors
                async def find_element_by_selectors(selectors):
                    """Try multiple selectors sequentially, return first match."""
                    for selector in selectors:
                        try:
                            loc = page.locator(selector)
                            await loc.first.wait_for(state="visible", timeout=10000)
                            return loc.first, selector
                        except:
                            continue
                    return None, None

                # Define selectors
                email_selectors = [
                    'input[type="email"]',
                    'input[autocomplete="username"]',
                    'input[placeholder*="example.com"]',
                    'xpath=//input[@type="email"]',
                ]

                button_selectors = [
                    'button[type="submit"]',
                    'button:has-text("Continue")',
                    '[data-testid*="primary-button"]',
                ]

                # Locate both elements in parallel
                results = await asyncio.gather(
                    find_element_by_selectors(email_selectors),
                    find_element_by_selectors(button_selectors),
                    return_exceptions=True
                )

                # Unpack results safely
                email_result = results[0] if not isinstance(results[0], Exception) else (None, None)
                button_result = results[1] if not isinstance(results[1], Exception) else (None, None)

                email_input, email_sel = email_result if isinstance(email_result, tuple) else (None, None)
                continue_btn, btn_sel = button_result if isinstance(button_result, tuple) else (None, None)

                if email_input:
                    log(f"Email input field located with selector: {email_sel}", "dbg")
                if continue_btn:
                    log(f"Continue button located with selector: {btn_sel}", "dbg")

                if not email_input:
                    log("Email input field not found after trying all known selectors on registration page", "err")
                    log(f"Page HTML sample for debugging: {(await page.content())[:500]}", "dbg")
                    await browser.close()
                    return _partial("email input not found")

                # Fill email
                await _move_to_element(page, email_input)
                await _human_type(page, email_input, email)
                await _human_delay(0.8, 1.5)
                log(f"Email filled: {email}", "ok")

                # Click Continue button (already located if found in parallel search)
                if continue_btn:
                    await continue_btn.click()
                    log("Clicked Continue button", "ok")
                else:
                    # Fallback: try to find button now if not found in parallel search
                    log("Continue button not pre-located, searching now...", "warn")
                    continue_clicked = False
                    for selector in button_selectors:
                        try:
                            btn = page.locator(selector)
                            if await btn.count() > 0:
                                await btn.first.click()
                                continue_clicked = True
                                log("Clicked Continue button (fallback)", "ok")
                                break
                        except:
                            pass

                    if not continue_clicked:
                        log("Trying JavaScript fallback for Continue button...", "warn")
                        await page.evaluate("""() => {
                            const btns = Array.from(document.querySelectorAll('button'));
                            const vis = btns.filter(b => b.offsetWidth > 0);
                            for (const b of vis) {
                                const t = (b.innerText || '').toLowerCase();
                                if (t.includes('continue') || t.includes('next')) {
                                    b.click(); return;
                                }
                            }
                        }""")

                # Monitor API responses after email Continue
                api_responses = {}

                async def handle_email_response(response):
                    url = response.url
                    try:
                        if '/get-config' in url:
                            api_responses['get-config'] = {
                                'status': response.status,
                                'body': await response.json()
                            }
                        elif '/start' in url:
                            api_responses['start'] = {
                                'status': response.status,
                                'body': await response.json()
                            }
                    except:
                        pass

                page.on("response", handle_email_response)
                await asyncio.sleep(4)

                # Log API responses
                if 'get-config' in api_responses:
                    cfg = api_responses['get-config']
                    log(f"[API] get-config -> {cfg['status']} {json.dumps(cfg['body'], separators=(',', ':'))}", "dbg")

                if 'start' in api_responses:
                    st = api_responses['start']
                    log(f"[API] start -> {st['status']} {json.dumps(st['body'], separators=(',', ':'))}", "dbg")

                page.remove_listener("response", handle_email_response)

                # Check if we're on the name input page
                log("Navigating to name input page after email submission", "info")
                name_input = None
                for selector in [
                    '[data-testid="signup-full-name-input"] input',
                    'input[type="text"][placeholder*="Silva"]',
                    'input[type="text"][autocomplete="on"]',
                ]:
                    try:
                        loc = page.locator(selector)
                        await loc.first.wait_for(state="visible", timeout=5000)
                        name_input = loc.first
                        log(f"Name input field located with selector: {selector}", "dbg")
                        break
                    except:
                        continue

                if name_input:
                    # Fill name
                    full_name = _generate_name()
                    log(f"Filling name: {full_name}", "info")
                    await _move_to_element(page, name_input)
                    await _human_type(page, name_input, full_name)
                    await _human_delay(0.8, 1.5)
                    log(f"Name filled: {full_name}", "ok")

                    # Click Continue button with TES block detection and retry
                    max_send_otp_retries = 4  # 4 attempts = initial + 3 retries (10s, 30s, 60s)
                    send_otp_success = False

                    for send_otp_attempt in range(max_send_otp_retries):
                        log("Looking for Continue button (name page)...", "info")

                        # Set up send-otp response listener
                        send_otp_response = {}

                        async def handle_sendotp_response(response):
                            if '/send-otp' in response.url:
                                try:
                                    send_otp_response['status'] = response.status
                                    send_otp_response['body'] = await response.json()
                                except:
                                    pass

                        page.on("response", handle_sendotp_response)

                        # Click Continue button
                        continue_clicked = False
                        for selector in [
                            '[data-testid="signup-next-button"]',
                            'button[type="submit"]',
                            'button:has-text("Continue")',
                        ]:
                            try:
                                btn = page.locator(selector)
                                if await btn.count() > 0:
                                    await btn.first.click()
                                    continue_clicked = True
                                    log("Clicked Continue button (name page)", "ok")
                                    break
                            except:
                                pass

                        if not continue_clicked:
                            log("Trying JavaScript fallback for Continue button (name page)...", "warn")
                            await page.evaluate("""() => {
                                const btns = Array.from(document.querySelectorAll('button'));
                                const vis = btns.filter(b => b.offsetWidth > 0);
                                for (const b of vis) {
                                    const t = (b.innerText || '').toLowerCase();
                                    if (t.includes('continue') || t.includes('next')) {
                                        b.click(); return;
                                    }
                                }
                            }""")

                        # Wait for send-otp API response
                        await asyncio.sleep(4)
                        page.remove_listener("response", handle_sendotp_response)

                        # Check if blocked by TES
                        if send_otp_response and send_otp_response.get('status') == 400:
                            body = send_otp_response.get('body', {})
                            log(f"[API] send-otp -> 400 {json.dumps(body, separators=(',', ':'))}", "dbg")

                            if body.get('errorCode') == 'BLOCKED':
                                if send_otp_attempt < max_send_otp_retries - 1:
                                    wait_times = [10, 30, 60]  # 10s, 30s, 60s
                                    wait_time = wait_times[send_otp_attempt] if send_otp_attempt < len(wait_times) else 60
                                    log(f"AWS TES blocked send-otp, retrying in {wait_time}s (attempt {send_otp_attempt + 2}/{max_send_otp_retries})", "warn")
                                    await asyncio.sleep(wait_time)
                                    continue  # Retry Continue button click
                                else:
                                    log(f"AWS TES blocked registration after {max_send_otp_retries} retry attempts", "err")
                                    await browser.close()
                                    return _partial("Blocked by AWS TES")
                            else:
                                # Other 400 error, not TES block
                                send_otp_success = True
                                break
                        else:
                            # Success or no response captured
                            if send_otp_response:
                                status = send_otp_response.get('status', '?')
                                body = send_otp_response.get('body', {})
                                log(f"[API] send-otp -> {status} {json.dumps(body, separators=(',', ':'))}", "dbg")
                            send_otp_success = True
                            break

                    if not send_otp_success:
                        log("Failed to navigate past name input page after form submission", "err")
                        await browser.close()
                        return _partial("Name page navigation failed")
                else:
                    log("No name input page detected, proceeding to OTP...", "info")

            # Phase 2.1: OTP verification (automated)
            log("Waiting for OTP verification page...", "info")
            await asyncio.sleep(3)

            # Find OTP input
            otp_input = None
            for selector in [
                '[data-testid="email-verification-form-code-input"]',
                'input[placeholder*="6-digit"]',
                'input[type="text"][autocomplete="on"]',
                'input[inputmode="numeric"]',
            ]:
                try:
                    loc = page.locator(selector)
                    await loc.first.wait_for(state="visible", timeout=10000)
                    otp_input = loc.first
                    log(f"Found OTP input with selector: {selector}", "ok")
                    break
                except:
                    continue

            if not otp_input:
                log("OTP input field not found on verification page after trying all known selectors", "err")
                await browser.close()
                return _partial("OTP input not found")

            # Get OTP from email
            log("Waiting for OTP code from email...", "info")
            log(f"Mail provider: {mail_provider_instance.__class__.__name__}", "dbg")
            otp_code = ""
            otp_deadline = time.time() + 90
            while time.time() < otp_deadline:
                if cancel_check and cancel_check():
                    await browser.close()
                    return None
                try:
                    otp_code = mail_provider_instance.wait_otp(timeout=5, poll_interval=3)
                    if otp_code:
                        break
                except Exception as e:
                    log(f"OTP polling error: {e}", "warn")
                    import traceback
                    traceback.print_exc()

            if not otp_code:
                # Try to resend OTP and retry once
                log("No OTP received, attempting to resend...", "warn")
                resend_clicked = False

                # Look for Resend button
                for selector in [
                    'button:has-text("Resend")',
                    'button:has-text("Send again")',
                    'a:has-text("Resend code")',
                    'a:has-text("Didn\'t receive")',
                ]:
                    try:
                        btn = page.locator(selector)
                        if await btn.count() > 0:
                            await btn.first.click()
                            resend_clicked = True
                            log("Resend OTP button clicked, waiting for new code", "info")
                            await asyncio.sleep(3)
                            break
                    except:
                        pass

                if resend_clicked:
                    # Retry waiting for OTP (60s second attempt)
                    log("Waiting for OTP code to arrive after resend request (60s timeout)", "info")
                    otp_deadline = time.time() + 60
                    while time.time() < otp_deadline:
                        if cancel_check and cancel_check():
                            await browser.close()
                            return None
                        try:
                            otp_code = mail_provider_instance.wait_otp(timeout=5, poll_interval=3)
                            if otp_code:
                                log(f"OTP received after resend: {otp_code}", "ok")
                                break
                        except Exception as e:
                            log(f"OTP polling error after resend: {e}", "warn")
                            import traceback
                            traceback.print_exc()

                if not otp_code:
                    log("OTP code not received within timeout even after resend attempt", "err")
                    await browser.close()
                    return _partial("OTP timeout")

            log(f"OTP code received from mail provider: {otp_code}", "info")

            # Fill OTP
            await _move_to_element(page, otp_input)
            await otp_input.click()
            await asyncio.sleep(0.3)
            for ch in otp_code:
                await page.keyboard.press(ch)
                await asyncio.sleep(0.05 + 0.1 * secrets.randbelow(10) / 10)

            log("OTP filled", "ok")

            # Click Continue button (OTP page)
            log("Looking for Continue button (OTP page)...", "info")
            continue_clicked = False
            for selector in [
                '[data-testid="email-verification-verify-button"]',
                'button[type="submit"]',
                'button:has-text("Continue")',
            ]:
                try:
                    btn = page.locator(selector)
                    if await btn.count() > 0:
                        await btn.first.click()
                        continue_clicked = True
                        log("Clicked Continue button (OTP page)", "ok")
                        break
                except:
                    pass

            if not continue_clicked:
                log("Trying JavaScript fallback for Continue button (OTP page)...", "warn")
                await page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const vis = btns.filter(b => b.offsetWidth > 0);
                    for (const b of vis) {
                        const t = (b.innerText || '').toLowerCase();
                        if (t.includes('continue') || t.includes('verify')) {
                            b.click(); return;
                        }
                    }
                }""")

            await asyncio.sleep(3)

            # Phase 2.2: Password creation (automated)
            log("Waiting for password creation page...", "info")

            # Wait for password page to load and find input (with retry)
            password_input = None
            password_deadline = time.time() + 60  # 60 second timeout
            attempt = 0

            while time.time() < password_deadline and not password_input:
                attempt += 1
                if attempt > 1:
                    log(f"Retry {attempt} - waiting for password input page to appear", "info")
                    await asyncio.sleep(3)

                for selector in [
                    '[data-testid="test-input"] input',
                    'input[type="password"][placeholder*="password"]',
                    'input[type="password"]',
                ]:
                    try:
                        loc = page.locator(selector).first
                        await loc.wait_for(state="visible", timeout=5000)  # 5 second timeout per selector
                        password_input = loc
                        log(f"Password input field located with selector: {selector}", "dbg")
                        break
                    except:
                        continue

                if password_input:
                    break

            if not password_input:
                log("Password input field not found after multiple retry attempts", "err")
                await browser.close()
                return _partial("Password input not found")

            # Generate password
            password = _generate_password()
            log(f"Filling generated password into input field (length: {len(password)})", "info")

            # Fill password
            await _move_to_element(page, password_input)
            await _human_type(page, password_input, password)
            await _human_delay(0.5, 1.0)

            # Find confirm password input (with retry)
            confirm_input = None
            confirm_deadline = time.time() + 30  # 30 second timeout
            attempt = 0

            while time.time() < confirm_deadline and not confirm_input:
                attempt += 1
                if attempt > 1:
                    log(f"Retry {attempt} - waiting for confirm password field...", "info")
                    await asyncio.sleep(2)

                for selector in [
                    '[data-testid="test-retype-input"] input',
                    'input[type="password"][placeholder*="Re-enter"]',
                    'input[type="password"][placeholder*="Confirm"]',
                ]:
                    try:
                        loc = page.locator(selector).first
                        await loc.wait_for(state="visible", timeout=3000)
                        confirm_input = loc
                        log(f"Found confirm password input with selector: {selector}", "ok")
                        break
                    except:
                        continue

                if confirm_input:
                    break

            if not confirm_input:
                log("Confirm password input field not found after multiple retry attempts", "err")
                await browser.close()
                return _partial("Confirm password input not found")

            # Fill confirm password
            await _move_to_element(page, confirm_input)
            await _human_type(page, confirm_input, password)
            await _human_delay(0.8, 1.5)
            log("Password filled", "ok")

            # Click Continue button (password page)
            log("Looking for Continue button (password page)...", "info")
            continue_clicked = False
            for selector in [
                '[data-testid="test-primary-button"]',
                'button[type="submit"]',
                'button:has-text("Continue")',
            ]:
                try:
                    btn = page.locator(selector)
                    if await btn.count() > 0:
                        await btn.first.click()
                        continue_clicked = True
                        log("Continue button clicked on password page to proceed", "info")
                        break
                except:
                    pass

            if not continue_clicked:
                log("Continue button not found via selectors, attempting JavaScript fallback on password page", "warn")
                await page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const vis = btns.filter(b => b.offsetWidth > 0);
                    for (const b of vis) {
                        const t = (b.innerText || '').toLowerCase();
                        if (t.includes('continue')) {
                            b.click(); return;
                        }
                    }
                }""")

            await asyncio.sleep(3)

            # Phase 2.3: Device authorization (automated)
            log("Waiting for device code confirmation page...", "info")
            await asyncio.sleep(3)

            # Step 1: Confirm device code
            confirm_button = None
            for selector in [
                '#cli_verification_btn',
                'button[data-analytics="accept-user-code"]',
                'button:has-text("Confirm and continue")',
            ]:
                try:
                    btn = page.locator(selector)
                    await btn.first.wait_for(state="visible", timeout=10000)
                    confirm_button = btn.first
                    log(f"Device code confirmation button located with selector: {selector}", "dbg")
                    break
                except:
                    continue

            if not confirm_button:
                log("Device code confirmation button not found on authorization page", "err")
                await browser.close()
                return _partial("Device code confirmation button not found")

            await confirm_button.click()
            log("Clicked 'Confirm and continue'", "ok")
            await asyncio.sleep(3)

            # Step 2: Allow access to Kiro
            log("Waiting for authorization consent page...", "info")
            allow_button = None
            for selector in [
                '[data-testid="allow-access-button"]',
                'button[data-analytics="consent-allow-access"]',
                'button:has-text("Allow access")',
            ]:
                try:
                    btn = page.locator(selector)
                    await btn.first.wait_for(state="visible", timeout=10000)
                    allow_button = btn.first
                    log(f"'Allow access' button located with selector: {selector}", "dbg")
                    break
                except:
                    continue

            if not allow_button:
                log("'Allow access' button not found on OAuth consent page", "err")
                await browser.close()
                return _partial("Allow access button not found")

            await allow_button.click()
            log("Clicked 'Allow access'", "ok")
            await asyncio.sleep(1)
            await browser.close()
            log("Playwright browser session closed successfully", "dbg")

        except Exception as e:
            log(f"Browser automation error during registration flow: {e}", "err")
            await browser.close()
            return _partial(f"browser: {e}")

    # Phase 3: Poll 9router
    log("Phase 3: Polling 9router API for account export completion", "info")
    poll = r9.poll_account(device_code, client_id, client_secret, code_verifier, log, 3)

    exported = poll["ok"]
    if exported:
        log("✅ Exported to 9router!", "ok")
    else:
        log(f"⚠️  Export failed: {poll.get('error', 'unknown')}", "warn")
        # Export failed = incomplete registration
        await browser.close()
        return _partial("9router export failed")

    # Phase 4: Local tokens
    if auto_login:
        try:
            persist_tokens(client_id, client_secret, "", "", 28800, log, email=email)
            if skip_onboard:
                skip_onboarding(log)
        except Exception as e:
            log(f"Token injection warning (non-fatal): {e}", "warn")

    log("=" * 40, "info")
    log("9router OAuth device code registration completed successfully!", "ok")
    log(f"  Account Email: {email}", "info")
    log(f"  9router Export Status: {exported}", "info")
    log("=" * 40, "info")

    return {
        "email": email,
        "password": password,
        "full_name": full_name,
        "provider": "BuilderId",
        "authMethod": "IdC",
        "region": region,
        "clientId": client_id,
        "clientSecret": client_secret,
        "clientIdHash": _sha1_hash(client_id),
        "accessToken": "",
        "refreshToken": "",
        "expiresAt": (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y/%m/%d %H:%M:%S"),
        "router9_exported": exported,
        "device_code_info": {
            "device_code": device_code,
            "verification_uri_complete": verify_url,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": code_verifier,
            "region": region,
            "user_code": user_code,
            "expires_at_timestamp": device_code_expires_at,
        },
    }
