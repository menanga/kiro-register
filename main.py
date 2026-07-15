#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kiro Pro Account manager
Features: SQLite storage + Account import/export + TokenRefresh + Quota query + Overage enabled + Local injection
Supports Github / Google (Social) and BuilderId (IdC) authentication
"""

import json
import os
import sys
import stat
import asyncio
import base64
import hashlib
import secrets
import queue
import re
import sqlite3
import time
import threading
import urllib.request
import urllib.error
import urllib.parse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlparse

# Load .env automatically so GUI users can set secrets without exporting manually.
def _load_dotenv():
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore[import]

        load_dotenv(dotenv_path=env_path)
    except Exception:
        # Minimal fallback if python-dotenv is not installed.
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"')
            if key and key not in os.environ:
                os.environ[key] = value

_load_dotenv()

# When running as PyInstaller bundle, configure paths for bundled dependencies
if getattr(sys, 'frozen', False):
    _bundle_dir = Path(sys._MEIPASS) if hasattr(sys, '_MEIPASS') else Path(sys.executable).parent
    # Bundled browsers location
    _bundled_browsers = _bundle_dir / 'ms-playwright'
    if _bundled_browsers.exists():
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(_bundled_browsers)
    else:
        # Fallback to user's installed browsers
        _pw_browsers = Path.home() / 'AppData' / 'Local' / 'ms-playwright'
        if _pw_browsers.exists():
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(_pw_browsers)
    # For onedir mode, the exe dir has all packages
    _exe_dir = Path(sys.executable).parent
    if _exe_dir not in [Path(p) for p in sys.path]:
        sys.path.insert(0, str(_exe_dir))
else:
    # Non-frozen: set browsers path if not already set
    if 'PLAYWRIGHT_BROWSERS_PATH' not in os.environ:
        _pw_browsers = Path.home() / 'AppData' / 'Local' / 'ms-playwright'
        if _pw_browsers.exists():
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(_pw_browsers)


# ─── Constants ───────────────────────────────────────────────────────────────

KIRO_AUTH_ENDPOINT = "https://prod.us-east-1.auth.desktop.kiro.dev"
CODEWHISPERER_ENDPOINT = "https://q.us-east-1.amazonaws.com"
SSO_OIDC_ENDPOINT = "https://oidc.{region}.amazonaws.com"

FIXED_PROFILE_ARNS = {
    "BuilderId": "arn:aws:codewhisperer:us-east-1:638616132270:profile/AAAACCCCXXXX",
    "Github": "arn:aws:codewhisperer:us-east-1:699475941385:profile/EHGA3GRVQMUK",
    "Google": "arn:aws:codewhisperer:us-east-1:699475941385:profile/EHGA3GRVQMUK",
}

KIRO_CACHE_DIR = Path.home() / ".aws" / "sso" / "cache"

# ─── Registration Constants ─────────────────────────────────────────────────

SHIROMAIL_BASE = os.environ.get("SHIROMAIL_BASE_URL", "https://shiromail.galiais.com")
SHIROMAIL_KEY = os.environ.get("SHIROMAIL_API_KEY", "")
SHIROMAIL_DOMAIN_ID = int(os.environ.get("SHIROMAIL_DOMAIN_ID", "4") or "4")
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

# DB path: same directory as exe (frozen) or script
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent

DB_PATH = Path(os.environ.get("DB_PATH", str(APP_DIR / "kiro_accounts.db")))
CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", str(APP_DIR / "kiro_config.json")))


def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# ─── Database Layer ──────────────────────────────────────────────────────────

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT,
    password TEXT DEFAULT '',
    provider TEXT,
    authMethod TEXT,
    accessToken TEXT,
    refreshToken TEXT,
    expiresAt TEXT,
    clientId TEXT,
    clientSecret TEXT,
    clientIdHash TEXT,
    region TEXT DEFAULT 'us-east-1',
    profileArn TEXT,
    userId TEXT,
    usageLimit INTEGER DEFAULT 0,
    currentUsage INTEGER DEFAULT 0,
    overageCap INTEGER DEFAULT 0,
    currentOverages INTEGER DEFAULT 0,
    overageStatus TEXT,
    overageCharges REAL DEFAULT 0.0,
    subscription TEXT DEFAULT '',
    lastQueryTime TEXT,
    createdAt TEXT DEFAULT (datetime('now','localtime')),
    updatedAt TEXT DEFAULT (datetime('now','localtime'))
);
"""


def get_db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(DB_SCHEMA)
    # Migration: add subscription column if missing
    cols = [r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()]
    if "subscription" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN subscription TEXT DEFAULT ''")
    if "password" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN password TEXT DEFAULT ''")
    conn.commit()
    return conn


def db_upsert_account(conn, data):
    """Insert or update account. Match by userId or email."""
    user_id = data.get("userId") or ""
    email = data.get("email") or ""

    existing = None
    if user_id:
        existing = conn.execute("SELECT id FROM accounts WHERE userId=?", (user_id,)).fetchone()
    if not existing and email:
        existing = conn.execute("SELECT id FROM accounts WHERE email=?", (email,)).fetchone()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    password = data.get("password", "")

    if existing:
        # Only update the password column when a new password is provided, to avoid overwriting the stored value
        if password:
            conn.execute("""
                UPDATE accounts SET
                    email=?, password=?, provider=?, authMethod=?, accessToken=?, refreshToken=?,
                    expiresAt=?, clientId=?, clientSecret=?, clientIdHash=?, region=?,
                    profileArn=?, userId=?, usageLimit=?, currentUsage=?, overageCap=?,
                    currentOverages=?, overageStatus=?, overageCharges=?, subscription=?,
                    lastQueryTime=?, updatedAt=?
                WHERE id=?
            """, (
                email, password, data.get("provider"), data.get("authMethod"),
                data.get("accessToken"), data.get("refreshToken"),
                data.get("expiresAt"), data.get("clientId"), data.get("clientSecret"),
                data.get("clientIdHash"), data.get("region", "us-east-1"),
                data.get("profileArn"), user_id,
                data.get("usageLimit", 0), data.get("currentUsage", 0),
                data.get("overageCap", 0), data.get("currentOverages", 0),
                data.get("overageStatus"), data.get("overageCharges", 0.0),
                data.get("subscription", ""), data.get("lastQueryTime"), now, existing["id"]
            ))
        else:
            conn.execute("""
                UPDATE accounts SET
                    email=?, provider=?, authMethod=?, accessToken=?, refreshToken=?,
                    expiresAt=?, clientId=?, clientSecret=?, clientIdHash=?, region=?,
                    profileArn=?, userId=?, usageLimit=?, currentUsage=?, overageCap=?,
                    currentOverages=?, overageStatus=?, overageCharges=?, subscription=?,
                    lastQueryTime=?, updatedAt=?
                WHERE id=?
            """, (
                email, data.get("provider"), data.get("authMethod"),
                data.get("accessToken"), data.get("refreshToken"),
                data.get("expiresAt"), data.get("clientId"), data.get("clientSecret"),
                data.get("clientIdHash"), data.get("region", "us-east-1"),
                data.get("profileArn"), user_id,
                data.get("usageLimit", 0), data.get("currentUsage", 0),
                data.get("overageCap", 0), data.get("currentOverages", 0),
                data.get("overageStatus"), data.get("overageCharges", 0.0),
                data.get("subscription", ""), data.get("lastQueryTime"), now, existing["id"]
            ))
    else:
        conn.execute("""
            INSERT INTO accounts (
                email, password, provider, authMethod, accessToken, refreshToken,
                expiresAt, clientId, clientSecret, clientIdHash, region,
                profileArn, userId, usageLimit, currentUsage, overageCap,
                currentOverages, overageStatus, overageCharges, subscription,
                lastQueryTime, createdAt, updatedAt
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            email, password, data.get("provider"), data.get("authMethod"),
            data.get("accessToken"), data.get("refreshToken"),
            data.get("expiresAt"), data.get("clientId"), data.get("clientSecret"),
            data.get("clientIdHash"), data.get("region", "us-east-1"),
            data.get("profileArn"), user_id,
            data.get("usageLimit", 0), data.get("currentUsage", 0),
            data.get("overageCap", 0), data.get("currentOverages", 0),
            data.get("overageStatus"), data.get("overageCharges", 0.0),
            data.get("subscription", ""), data.get("lastQueryTime"), now, now
        ))
    conn.commit()


def db_get_all(conn):
    return conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()


def db_delete(conn, row_id):
    conn.execute("DELETE FROM accounts WHERE id=?", (row_id,))
    conn.commit()


def db_update_usage(conn, row_id, usage_data):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        UPDATE accounts SET
            usageLimit=?, currentUsage=?, overageCap=?, currentOverages=?,
            overageStatus=?, overageCharges=?, subscription=?, lastQueryTime=?, updatedAt=?
        WHERE id=?
    """, (
        usage_data.get("usageLimit", 0), usage_data.get("currentUsage", 0),
        usage_data.get("overageCap", 0), usage_data.get("currentOverages", 0),
        usage_data.get("overageStatus"), usage_data.get("overageCharges", 0.0),
        usage_data.get("subscription", ""), now, now, row_id
    ))
    conn.commit()


def db_update_token(conn, row_id, access_token, refresh_token, expires_at):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        UPDATE accounts SET accessToken=?, refreshToken=?, expiresAt=?, updatedAt=?
        WHERE id=?
    """, (access_token, refresh_token, expires_at, now, row_id))
    conn.commit()


# ─── API Layer ───────────────────────────────────────────────────────────────

def http_post(url, body, headers=None):
    all_headers = {"Content-Type": "application/json"}
    if headers:
        all_headers.update(headers)
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=all_headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {"ok": True, "data": json.loads(resp.read()), "status": resp.status}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        try:
            err_data = json.loads(error_body)
        except Exception:
            err_data = {"raw": error_body}
        return {"ok": False, "error": err_data, "status": e.code}
    except Exception as e:
        return {"ok": False, "error": {"message": str(e)}, "status": 0}


def http_get(url, headers=None):
    all_headers = {}
    if headers:
        all_headers.update(headers)
    req = urllib.request.Request(url, headers=all_headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {"ok": True, "data": json.loads(resp.read()), "status": resp.status}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        try:
            err_data = json.loads(error_body)
        except Exception:
            err_data = {"raw": error_body}
        return {"ok": False, "error": err_data, "status": e.code}
    except Exception as e:
        return {"ok": False, "error": {"message": str(e)}, "status": 0}


def refresh_social_token(refresh_token):
    url = f"{KIRO_AUTH_ENDPOINT}/refreshToken"
    body = {"refreshToken": refresh_token}
    result = http_post(url, body)
    if result["ok"]:
        d = result["data"]
        return {
            "accessToken": d["accessToken"],
            "refreshToken": d["refreshToken"],
            "expiresIn": d.get("expiresIn", 3600),
        }
    return None


def refresh_idc_token(client_id, client_secret, refresh_token, region="us-east-1"):
    url = f"{SSO_OIDC_ENDPOINT.format(region=region)}/token"
    body = {
        "clientId": client_id,
        "clientSecret": client_secret,
        "refreshToken": refresh_token,
        "grantType": "refresh_token",
    }
    result = http_post(url, body)
    if result["ok"]:
        d = result["data"]
        return {
            "accessToken": d["accessToken"],
            "refreshToken": d["refreshToken"],
            "expiresIn": d.get("expiresIn", 3600),
            "idToken": d.get("idToken", ""),
        }
    return None


def decode_jwt_email(token):
    """Try to extract email from a JWT token (access or id token)."""
    if not token:
        return "", ""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return "", ""
        payload = parts[1]
        payload += "=" * (4 - len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        email = claims.get("email", "") or claims.get("preferred_username", "")
        user_id = claims.get("sub", "")
        return email, user_id
    except Exception:
        return "", ""


def get_userinfo_email(access_token, region="us-east-1"):
    """Try OIDC userinfo endpoint to get email for IdC/BuilderId accounts."""
    for base in [
        f"https://oidc.{region}.amazonaws.com",
        "https://identitystore.us-east-1.amazonaws.com",
    ]:
        url = f"{base}/userinfo"
        result = http_get(url, {"Authorization": f"Bearer {access_token}"})
        if result["ok"]:
            data = result["data"]
            email = data.get("email", "") or data.get("preferred_username", "") or data.get("name", "")
            user_id = data.get("sub", "")
            if email:
                return email, user_id
    return "", ""


def query_usage(access_token, profile_arn, require_email=False):
    params = {"profileArn": profile_arn}
    if require_email:
        params["isEmailRequired"] = "true"
    url = f"{CODEWHISPERER_ENDPOINT}/getUsageLimits?{urllib.parse.urlencode(params)}"
    headers = {"Authorization": f"Bearer {access_token}"}
    return http_get(url, headers)


def enable_overage(access_token, profile_arn):
    url = f"{CODEWHISPERER_ENDPOINT}/setUserPreference"
    headers = {"Authorization": f"Bearer {access_token}"}
    body = {"profileArn": profile_arn, "overageConfiguration": {"overageStatus": "ENABLED"}}
    return http_post(url, body, headers)


def list_available_models(access_token, profile_arn):
    """GET /ListAvailableModels - returns list of supported models."""
    params = {"origin": "AI_EDITOR", "profileArn": profile_arn}
    url = f"{CODEWHISPERER_ENDPOINT}/ListAvailableModels?{urllib.parse.urlencode(params)}"
    headers = {"Authorization": f"Bearer {access_token}"}
    all_models = []
    default_model = None
    while True:
        result = http_get(url, headers)
        if not result["ok"]:
            return {"ok": False, "models": [], "defaultModel": None, "error": result.get("error")}
        data = result["data"]
        all_models.extend(data.get("models", []))
        if not default_model and data.get("defaultModel"):
            default_model = data["defaultModel"]
        next_token = data.get("nextToken")
        if not next_token:
            break
        params["nextToken"] = next_token
        url = f"{CODEWHISPERER_ENDPOINT}/ListAvailableModels?{urllib.parse.urlencode(params)}"
    return {"ok": True, "models": all_models, "defaultModel": default_model}


def list_profiles(access_token):
    url = f"{CODEWHISPERER_ENDPOINT}/ListAvailableProfiles"
    headers = {"Authorization": f"Bearer {access_token}"}
    result = http_post(url, {}, headers)
    if result["ok"]:
        profiles = result["data"].get("profiles", [])
        if profiles:
            return profiles[0].get("arn")
    return None


# ─── Token Helpers ───────────────────────────────────────────────────────────

SUBSCRIPTION_DISPLAY = {
    "KIRO_PRO": "Pro",
    "KIRO_FREE": "Free",
    "KIRO_POWER": "Power",
    "KIRO_PRO_PLUS": "Pro+",
    "Q_DEVELOPER_STANDALONE_PRO": "Pro",
    "Q_DEVELOPER_STANDALONE_FREE": "Free",
    "Q_DEVELOPER_STANDALONE_POWER": "Power",
    "Q_DEVELOPER_STANDALONE_PRO_PLUS": "Pro+",
    "Q_DEVELOPER_STANDALONE": "Free",
}

# Kiro API Error code mapping (source: kiro-agent/q-client)
API_ERROR_MESSAGES = {
    "AccessDeniedException": "Access denied",
    "FEATURE_NOT_SUPPORTED": "Feature not supported (account type mismatch))",
    "TEMPORARILY_SUSPENDED": "Account is temporarily banned",
    "ThrottlingException": "Requests too frequent",
    "INSUFFICIENT_MODEL_CAPACITY": "Insufficient model capacity",
    "ServiceQuotaExceededException": "Service quota exceeded",
    "OVERAGE_REQUEST_LIMIT_EXCEEDED": "Overage request limit reached",
    "ValidationException": "Invalid request parameters",
    "INVALID_MODEL_ID": "Invalid model ID",
    "InternalServerException": "Internal server error",
    "MODEL_TEMPORARILY_UNAVAILABLE": "Model is temporarily unavailable",
    "UnsupportedClientVersionException": "Client version unsupported",
    "HOURLY_REQUEST_COUNT": "Hourly request limit reached",
    "DAILY_REQUEST_COUNT": "Daily request limit reached",
    "WEEKLY_REQUEST_COUNT": "Weekly request limit reached",
    "MONTHLY_REQUEST_COUNT": "Monthly request limit reached",
    "USAGE_LIMIT_REACHED": "Usage limit reached",
    "Operation not supported": "Operation not supported (Free plan cannot enable overage))",
}


def translate_api_error(err_data):
    """Convert API errors into human-readable messages"""
    if isinstance(err_data, dict):
        msg = err_data.get("message") or err_data.get("Message") or ""
        reason = err_data.get("reason") or ""
        err_type = err_data.get("__type") or err_data.get("type") or ""
        for key, zh in API_ERROR_MESSAGES.items():
            if key in msg or key in reason or key in err_type:
                return zh
        return msg[:80] if msg else str(err_data)[:80]
    return str(err_data)[:80]


def format_subscription(raw):
    """Convert raw subscription title/type to display name."""
    if not raw:
        return "-"
    upper = raw.upper().replace(" ", "_")
    for key, display in SUBSCRIPTION_DISPLAY.items():
        if key in upper:
            return display
    if "PRO" in upper:
        return "Pro"
    if "FREE" in upper:
        return "Free"
    return raw


def is_token_expired(expires_at_str):
    """Check if token is expired or will expire within 5 minutes."""
    if not expires_at_str:
        return True
    for fmt in ("%Y-%m-%dT%H:%M:%S.000Z", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(expires_at_str, fmt)
            return dt < datetime.now() + timedelta(minutes=5)
        except ValueError:
            continue
    return True


def do_refresh_token(row):
    """Refresh token for a DB row. Returns (access_token, refresh_token, expires_at, error)."""
    auth_method = row["authMethod"]
    if auth_method == "social":
        result = refresh_social_token(row["refreshToken"])
        if result:
            expires_at = (datetime.now() + timedelta(seconds=result["expiresIn"])).strftime("%Y-%m-%d %H:%M:%S")
            return result["accessToken"], result["refreshToken"], expires_at, None
        return None, None, None, "Social Token Refresh failed"
    elif auth_method == "IdC":
        client_id = row["clientId"]
        client_secret = row["clientSecret"]
        region = row["region"] or "us-east-1"
        if not client_id or not client_secret:
            return None, None, None, "missing clientId/clientSecret"
        result = refresh_idc_token(client_id, client_secret, row["refreshToken"], region)
        if result:
            expires_at = (datetime.now() + timedelta(seconds=result["expiresIn"])).strftime("%Y-%m-%d %H:%M:%S")
            return result["accessToken"], result["refreshToken"], expires_at, None
        return None, None, None, "IdC Token Refresh failed"
    return None, None, None, f"Unknown authentication method: {auth_method}"


def get_valid_token(row, conn=None):
    """Get a valid access token, refreshing if needed. Optionally updates DB and subscription."""
    if not is_token_expired(row["expiresAt"]):
        return row["accessToken"], None
    access_token, refresh_token, expires_at, err = do_refresh_token(row)
    if err:
        return None, err
    if conn and row["id"]:
        db_update_token(conn, row["id"], access_token, refresh_token, expires_at)
        # Sync subscription after refresh
        _sync_subscription_after_refresh(conn, row, access_token)
    return access_token, None


def _sync_subscription_after_refresh(conn, row, access_token):
    """Query usage after token refresh to update subscription type."""
    provider = row["provider"] or ""
    profile_arn = row["profileArn"] or FIXED_PROFILE_ARNS.get(provider, "")
    if not profile_arn:
        profile_arn = list_profiles(access_token) or ""
    if not profile_arn:
        return
    result = query_usage(access_token, profile_arn)
    if result["ok"]:
        data = result["data"]
        bl = data.get("usageBreakdownList", [])
        b = bl[0] if bl else {}
        sub_info = data.get("subscriptionInfo", {})
        sub_raw = sub_info.get("subscriptionTitle", "") or sub_info.get("type", "") if sub_info else ""
        db_update_usage(conn, row["id"], {
            "usageLimit": int(b.get("usageLimit", b.get("usageLimitWithPrecision", 0))),
            "currentUsage": int(b.get("currentUsage", b.get("currentUsageWithPrecision", 0))),
            "overageCap": int(b.get("overageCap", b.get("overageCapWithPrecision", 0))),
            "currentOverages": int(b.get("currentOverages", b.get("currentOveragesWithPrecision", 0))),
            "overageStatus": data.get("overageConfiguration", {}).get("overageStatus", ""),
            "overageCharges": float(b.get("overageCharges", 0)),
            "subscription": sub_raw,
        })


# ─── Inject Logic ────────────────────────────────────────────────────────────

def parse_expires_for_inject(expires_str):
    if not expires_str:
        return (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.000Z"):
        try:
            dt = datetime.strptime(expires_str, fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except ValueError:
            continue
    return (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_client_secret_expiry(client_secret):
    try:
        parts = client_secret.split(".")
        payload = parts[1]
        payload += "=" * (4 - len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        serialized = json.loads(decoded["serialized"])
        ts = serialized.get("expirationTimestamp", 0)
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except Exception:
        return (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def inject_account(row):
    """Inject account to local Kiro cache. row is a dict or sqlite3.Row."""
    KIRO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    auth_method = row["authMethod"] or ""
    provider = row["provider"] or ""

    token_data = {
        "accessToken": row["accessToken"],
        "refreshToken": row["refreshToken"],
        "expiresAt": parse_expires_for_inject(row["expiresAt"]),
    }

    if auth_method == "social":
        token_data["authMethod"] = "social"
        token_data["provider"] = provider
    elif auth_method == "IdC":
        token_data["authMethod"] = "IdC"
        token_data["provider"] = provider
        token_data["region"] = row["region"] or "us-east-1"
        token_data["clientIdHash"] = row["clientIdHash"] or ""
    else:
        return False, f"Unsupported authentication method: {auth_method}"

    token_path = KIRO_CACHE_DIR / "kiro-auth-token.json"
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)
    try:
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass

    if auth_method == "IdC" and row["clientId"] and row["clientSecret"]:
        client_reg = {
            "clientId": row["clientId"],
            "clientSecret": row["clientSecret"],
            "expiresAt": parse_client_secret_expiry(row["clientSecret"]),
        }
        client_hash = row["clientIdHash"] or ""
        if client_hash:
            client_path = KIRO_CACHE_DIR / f"{client_hash}.json"
            with open(client_path, "w", encoding="utf-8") as f:
                json.dump(client_reg, f, indent=2)
            try:
                os.chmod(client_path, stat.S_IRUSR | stat.S_IWUSR)
            except Exception:
                pass

    return True, f"Injection succeeded ({provider}/{auth_method})"


def get_local_token_status():
    token_path = KIRO_CACHE_DIR / "kiro-auth-token.json"
    if not token_path.exists():
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ─── Import Helpers ──────────────────────────────────────────────────────────

def import_from_local_kiro(conn):
    """Read local kiro-auth-token.json and clientRegistration, query usage, save to DB."""
    token = get_local_token_status()
    if not token:
        return False, "No local install found kiro-auth-token.json"

    auth_method = token.get("authMethod", "")
    provider = token.get("provider", "")
    access_token = token.get("accessToken", "")
    refresh_token = token.get("refreshToken", "")
    expires_at = token.get("expiresAt", "")
    region = token.get("region", "us-east-1")
    client_id_hash = token.get("clientIdHash", "")

    client_id = ""
    client_secret = ""

    if auth_method == "IdC" and client_id_hash:
        client_path = KIRO_CACHE_DIR / f"{client_id_hash}.json"
        if client_path.exists():
            try:
                with open(client_path, "r", encoding="utf-8") as f:
                    client_data = json.load(f)
                client_id = client_data.get("clientId", "")
                client_secret = client_data.get("clientSecret", "")
            except Exception:
                pass

    # Normalize expiresAt
    norm_expires = expires_at
    if expires_at and "T" in expires_at:
        try:
            dt = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%S.000Z")
            norm_expires = dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    # Determine profile ARN
    profile_arn = FIXED_PROFILE_ARNS.get(provider, "")
    if not profile_arn and access_token:
        profile_arn = list_profiles(access_token) or ""

    # Query usage
    usage_limit = 0
    current_usage = 0
    overage_cap = 0
    current_overages = 0
    overage_status = ""
    overage_charges = 0.0
    subscription = ""
    email = ""
    user_id = ""

    if access_token and profile_arn:
        result = query_usage(access_token, profile_arn, require_email=True)
        if result["ok"]:
            data = result["data"]
            # Extract email from userInfo in response
            user_info = data.get("userInfo", {})
            if user_info:
                email = user_info.get("email", "")
            # Extract subscription info
            sub_info = data.get("subscriptionInfo", {})
            if sub_info:
                subscription = sub_info.get("subscriptionTitle", "") or sub_info.get("type", "")
            breakdown_list = data.get("usageBreakdownList", [])
            if breakdown_list:
                b = breakdown_list[0]
                usage_limit = int(b.get("usageLimit", b.get("usageLimitWithPrecision", 0)))
                current_usage = int(b.get("currentUsage", b.get("currentUsageWithPrecision", 0)))
                overage_cap = int(b.get("overageCap", b.get("overageCapWithPrecision", 0)))
                current_overages = int(b.get("currentOverages", b.get("currentOveragesWithPrecision", 0)))
                overage_charges = float(b.get("overageCharges", 0))
            overage_cfg = data.get("overageConfiguration", {})
            overage_status = overage_cfg.get("overageStatus", "")

    # If email not from API response, try JWT decode (works for social)
    if not email:
        email, user_id = decode_jwt_email(access_token)

    # For IdC, access token is opaque; try refreshing to get idToken with email
    if not email and auth_method == "IdC" and client_id and client_secret and refresh_token:
        refresh_result = refresh_idc_token(client_id, client_secret, refresh_token, region)
        if refresh_result:
            access_token = refresh_result["accessToken"]
            refresh_token = refresh_result["refreshToken"]
            norm_expires = (datetime.now() + timedelta(seconds=refresh_result["expiresIn"])).strftime("%Y-%m-%d %H:%M:%S")
            id_token = refresh_result.get("idToken", "")
            if id_token:
                email, user_id = decode_jwt_email(id_token)

    # Still no email? Try OIDC userinfo endpoint
    if not email and access_token:
        email, user_id = get_userinfo_email(access_token, region)

    if not email:
        email = f"{provider}_{auth_method}_local"

    account_data = {
        "email": email,
        "provider": provider,
        "authMethod": auth_method,
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": norm_expires,
        "clientId": client_id,
        "clientSecret": client_secret,
        "clientIdHash": client_id_hash,
        "region": region,
        "profileArn": profile_arn,
        "userId": user_id,
        "usageLimit": usage_limit,
        "currentUsage": current_usage,
        "overageCap": overage_cap,
        "currentOverages": current_overages,
        "overageStatus": overage_status,
        "overageCharges": overage_charges,
        "subscription": subscription,
        "lastQueryTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S") if profile_arn else None,
    }

    db_upsert_account(conn, account_data)
    return True, f"Import succeeded: {email} ({provider})"


def import_from_json_file(conn, file_path):
    """Import accounts from JSON file (array of account objects).
    Returns (count, emails_list) for selective refresh.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        accounts = json.load(f)

    if not isinstance(accounts, list):
        accounts = [accounts]

    imported = 0
    imported_emails = []
    for acc in accounts:
        usage_data = acc.get("usageData", {})
        breakdown_list = usage_data.get("usageBreakdownList", [])
        b = breakdown_list[0] if breakdown_list else {}

        overage_cfg = usage_data.get("overageConfiguration", {})

        email = acc.get("email", "")
        account_data = {
            "email": email,
            "provider": acc.get("provider", ""),
            "authMethod": acc.get("authMethod", ""),
            "accessToken": acc.get("accessToken", ""),
            "refreshToken": acc.get("refreshToken", ""),
            "expiresAt": acc.get("expiresAt", ""),
            "clientId": acc.get("clientId", ""),
            "clientSecret": acc.get("clientSecret", ""),
            "clientIdHash": acc.get("clientIdHash", ""),
            "region": acc.get("region", "us-east-1"),
            "profileArn": acc.get("profileArn", ""),
            "userId": acc.get("userId", ""),
            "usageLimit": int(b.get("usageLimit", b.get("usageLimitWithPrecision", 0))),
            "currentUsage": int(b.get("currentUsage", b.get("currentUsageWithPrecision", 0))),
            "overageCap": int(b.get("overageCap", b.get("overageCapWithPrecision", 0))),
            "currentOverages": int(b.get("currentOverages", b.get("currentOveragesWithPrecision", 0))),
            "overageStatus": overage_cfg.get("overageStatus", ""),
            "overageCharges": float(b.get("overageCharges", 0)),
            "lastQueryTime": None,
        }
        db_upsert_account(conn, account_data)
        imported += 1
        if email:
            imported_emails.append(email)

    return imported, imported_emails


def export_to_json(conn, file_path):
    """Export all accounts from DB to JSON file."""
    rows = db_get_all(conn)
    accounts = []
    for row in rows:
        acc = {
            "email": row["email"],
            "provider": row["provider"],
            "authMethod": row["authMethod"],
            "accessToken": row["accessToken"],
            "refreshToken": row["refreshToken"],
            "expiresAt": row["expiresAt"],
            "clientId": row["clientId"],
            "clientSecret": row["clientSecret"],
            "clientIdHash": row["clientIdHash"],
            "region": row["region"],
            "profileArn": row["profileArn"],
            "userId": row["userId"],
            "usageData": {
                "usageBreakdownList": [{
                    "usageLimit": row["usageLimit"],
                    "currentUsage": row["currentUsage"],
                    "overageCap": row["overageCap"],
                    "currentOverages": row["currentOverages"],
                    "overageCharges": row["overageCharges"],
                }],
                "overageConfiguration": {
                    "overageStatus": row["overageStatus"] or "",
                },
            },
        }
        accounts.append(acc)

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2, ensure_ascii=False)
    return len(accounts)


# ─── GUI Application ─────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Kiro Pro Account manager | For study and research only; selling, redistributing or any commercial use is forbidden")
        self.geometry("1100x720")
        self.minsize(900, 600)
        self.configure(bg="#1a1a2e")

        self.conn = get_db()
        self.running = False
        self._lock = threading.Lock()
        self._auto_refresh_id = None

        self._setup_styles()
        self._build_ui()
        self._load_accounts_from_db()
        self._refresh_local_status()
        self._start_auto_refresh()

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(".", background="#1a1a2e", foreground="#e0e0e0")
        style.configure("TFrame", background="#1a1a2e")
        style.configure("TLabelframe", background="#1a1a2e", foreground="#e0e0e0")
        style.configure("TLabelframe.Label", background="#1a1a2e", foreground="#00d2ff",
                        font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TLabel", background="#1a1a2e", foreground="#e0e0e0",
                        font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 14, "bold"), foreground="#00d2ff")
        style.configure("Stats.TLabel", font=("Microsoft YaHei UI", 9), foreground="#b0b0b0")
        style.configure("Status.TLabel", font=("Consolas", 9), foreground="#8b949e")

        style.configure("TButton", font=("Microsoft YaHei UI", 9), padding=(10, 5))
        style.map("TButton",
            background=[("active", "#16213e"), ("!active", "#0f3460")],
            foreground=[("active", "#ffffff"), ("!active", "#e0e0e0")],
        )
        style.configure("Green.TButton", font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Green.TButton",
            background=[("active", "#1b8a5a"), ("!active", "#2ecc71")],
            foreground=[("active", "#ffffff"), ("!active", "#1a1a2e")],
        )
        style.configure("Orange.TButton", font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Orange.TButton",
            background=[("active", "#d68910"), ("!active", "#f39c12")],
            foreground=[("active", "#ffffff"), ("!active", "#1a1a2e")],
        )
        style.configure("Red.TButton", font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Red.TButton",
            background=[("active", "#c0392b"), ("!active", "#f85149")],
            foreground=[("active", "#ffffff"), ("!active", "#1a1a2e")],
        )

        style.configure("Treeview",
            background="#16213e", foreground="#e0e0e0", fieldbackground="#16213e",
            font=("Consolas", 9), rowheight=24,
        )
        style.configure("Treeview.Heading",
            background="#0f3460", foreground="#00d2ff",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map("Treeview", background=[("selected", "#1a5276")])

        style.configure("TNotebook", background="#1a1a2e")
        style.configure("TNotebook.Tab", font=("Microsoft YaHei UI", 10), padding=(15, 5))
        style.map("TNotebook.Tab",
            background=[("selected", "#0f3460"), ("!selected", "#16213e")],
            foreground=[("selected", "#00d2ff"), ("!selected", "#8b949e")],
        )
        style.configure("TEntry",
            fieldbackground="#16213e", foreground="#e0e0e0", insertcolor="#e0e0e0",
            font=("Consolas", 9))
        style.configure("TCombobox",
            fieldbackground="#16213e", foreground="#e0e0e0",
            background="#0f3460", arrowcolor="#00d2ff",
            font=("Microsoft YaHei UI", 9))
        style.map("TCombobox",
            fieldbackground=[("readonly", "#16213e"), ("disabled", "#111122")],
            foreground=[("readonly", "#e0e0e0"), ("disabled", "#666666")],
            selectbackground=[("readonly", "#1a5276")],
            selectforeground=[("readonly", "#ffffff")],
        )
        self.option_add("*TCombobox*Listbox.background", "#16213e")
        self.option_add("*TCombobox*Listbox.foreground", "#e0e0e0")
        self.option_add("*TCombobox*Listbox.selectBackground", "#1a5276")
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        style.configure("TCheckbutton", background="#1a1a2e", foreground="#e0e0e0",
            font=("Microsoft YaHei UI", 9))
        style.map("TCheckbutton",
            background=[("active", "#1a1a2e"), ("!active", "#1a1a2e")],
            foreground=[("active", "#00d2ff"), ("!active", "#e0e0e0")],
            indicatorcolor=[("selected", "#00d2ff"), ("!selected", "#16213e")],
        )
        style.configure("Horizontal.TProgressbar", troughcolor="#16213e", background="#2ecc71")

    def _build_ui(self):
        header = ttk.Frame(self, padding=(20, 12))
        header.pack(fill="x")
        ttk.Label(header, text="Kiro Pro Account manager", style="Title.TLabel").pack(side="left")
        self.lbl_db_path = ttk.Label(header, text=f"Database: {DB_PATH.name}", style="Stats.TLabel")
        self.lbl_db_path.pack(side="right")

        warn_frame = tk.Frame(self, bg="#1a1a2e")
        warn_frame.pack(fill="x", padx=20)
        tk.Label(warn_frame, text="⚠ This software is for study and research only. Selling, redistributing, or any commercial use is strictly prohibited; violators are solely responsible.",
                 bg="#1a1a2e", fg="#f85149", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        self._build_tab_accounts()
        self._build_tab_local()
        self._build_tab_register()
        self._build_tab_manual_login()

    # ─── Tab 1: Account manager ─────────────────────────────────────────────────
    def _build_tab_accounts(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  Account manager  ")

        toolbar = ttk.Frame(tab)
        toolbar.pack(fill="x", pady=(0, 8))

        ttk.Button(toolbar, text="Import from local Kiro",
                   command=self._import_local).pack(side="left", padx=(0, 5))
        ttk.Button(toolbar, text="Import from JSON",
                   command=self._import_json).pack(side="left", padx=5)
        ttk.Button(toolbar, text="ExportJSON", style="Green.TButton",
                   command=self._export_json).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Delete selected", style="Red.TButton",
                   command=self._delete_selected).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Refresh selected",
                   command=self._refresh_selected_token).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Refresh all",
                   command=self._refresh_all_tokens).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Health check", style="Orange.TButton",
                   command=self._health_check).pack(side="left", padx=5)

        toolbar2 = ttk.Frame(tab)
        toolbar2.pack(fill="x", pady=(0, 4))
        ttk.Button(toolbar2, text="Select all",
                   command=self._select_all_accounts).pack(side="left", padx=(0, 5))
        ttk.Button(toolbar2, text="Invert selection",
                   command=self._invert_selection).pack(side="left", padx=5)
        ttk.Button(toolbar2, text="Copy selectedJSON",
                   command=self._copy_selected_json).pack(side="left", padx=5)
        ttk.Button(toolbar2, text="Copy selected emails",
                   command=self._copy_selected_emails).pack(side="left", padx=5)
        ttk.Button(toolbar2, text="Query selected quota",
                   command=self._query_selected_usage).pack(side="left", padx=5)
        ttk.Button(toolbar2, text="Query all quotas",
                   command=self._query_all_usage).pack(side="left", padx=5)
        ttk.Button(toolbar2, text="Bulk enable overage", style="Green.TButton",
                   command=self._batch_enable_overage).pack(side="left", padx=5)
        ttk.Button(toolbar2, text="Inject the selected account locally", style="Orange.TButton",
                   command=self._inject_selected).pack(side="left", padx=5)
        self.lbl_sel_info = ttk.Label(toolbar2, text="", style="Stats.TLabel")
        self.lbl_sel_info.pack(side="right")

        cfg = load_config()
        ttk.Label(toolbar, text="Auto-refresh:").pack(side="left", padx=(15, 0))
        self._auto_refresh_min = tk.StringVar(value=cfg.get("auto_refresh_min", "60"))
        ttk.Entry(toolbar, textvariable=self._auto_refresh_min, width=4).pack(side="left", padx=2)
        ttk.Label(toolbar, text="min").pack(side="left")
        def _on_refresh_change(*_):
            c = load_config()
            c["auto_refresh_min"] = self._auto_refresh_min.get().strip()
            save_config(c)
            self._start_auto_refresh()
        self._auto_refresh_min.trace_add("write", _on_refresh_change)

        self.lbl_acc_stats = ttk.Label(toolbar, text="", style="Stats.TLabel")
        self.lbl_acc_stats.pack(side="right")

        self.acc_progress = ttk.Progressbar(tab, mode="determinate",
                                            style="Horizontal.TProgressbar")
        self.acc_progress.pack(fill="x", pady=(0, 8))

        # PanedWindow: top = account tree, bottom = models + log
        paned = tk.PanedWindow(tab, orient=tk.VERTICAL, sashwidth=6,
                               bg="#1a1a2e", sashrelief="flat", opaqueresize=False)
        paned.pack(fill="both", expand=True)

        # Top pane: account tree
        table_frame = ttk.Frame(paned)
        columns = ("id", "email", "provider", "auth", "subscription", "overage", "usage", "expires", "status")
        self.acc_tree = ttk.Treeview(table_frame, columns=columns, show="headings",
                                     selectmode="extended")
        col_cfg = [
            ("id", "ID", 35), ("email", "Email", 180), ("provider", "Sign-in method", 70),
            ("auth", "Auth type", 60), ("subscription", "Subscription", 80),
            ("overage", "Overage status", 75), ("usage", "Usage", 90),
            ("expires", "Tokenexpired", 130), ("status", "Status", 80),
        ]
        for cid, heading, width in col_cfg:
            self.acc_tree.heading(cid, text=heading)
            self.acc_tree.column(cid, width=width, minwidth=35)

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.acc_tree.yview)
        self.acc_tree.configure(yscrollcommand=scrollbar.set)
        self.acc_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        paned.add(table_frame, minsize=100)

        # Bottom pane: models + log
        bottom_frame = ttk.Frame(paned)

        # Models detail panel (collapsible)
        self._models_visible = tk.BooleanVar(value=False)
        self._models_cache = {}

        models_header = ttk.Frame(bottom_frame)
        models_header.pack(fill="x", pady=(4, 0))
        self.btn_models_toggle = ttk.Button(models_header, text="▶ Available models (click to expand))",
                                            command=self._toggle_models_panel)
        self.btn_models_toggle.pack(side="left")
        ttk.Button(models_header, text="Query models",
                   command=self._query_selected_models).pack(side="left", padx=8)

        self.models_frame = ttk.Frame(bottom_frame)
        self.models_text = tk.Text(self.models_frame, bg="#0d1117", fg="#e0e0e0",
                                   font=("Consolas", 9), insertbackground="#e0e0e0",
                                   relief="flat", wrap="word", height=8)
        models_scroll = ttk.Scrollbar(self.models_frame, orient="vertical",
                                      command=self.models_text.yview)
        self.models_text.configure(yscrollcommand=models_scroll.set)
        self.models_text.pack(side="left", fill="both", expand=True)
        models_scroll.pack(side="right", fill="y")
        self.models_text.tag_configure("title", foreground="#00d2ff", font=("Consolas", 9, "bold"))
        self.models_text.tag_configure("default", foreground="#2ecc71")
        self.models_text.tag_configure("model", foreground="#e0e0e0")
        self.models_text.tag_configure("dim", foreground="#8b949e")

        self.acc_tree.bind("<<TreeviewSelect>>", self._on_acc_select)
        self.acc_tree.bind("<Button-3>", self._on_acc_right_click)
        self.acc_tree.bind("<Double-1>", self._on_acc_double_click)
        self.acc_tree.bind("<Control-a>", lambda e: self._select_all_accounts())
        self.acc_tree.bind("<Control-c>", lambda e: self._copy_selected_json())
        self.acc_tree.bind("<Delete>", lambda e: self._delete_selected())

        # Log panel
        self._log_label = ttk.Label(bottom_frame, text="Operation log", style="Stats.TLabel")
        self._log_label.pack(anchor="w", pady=(8, 2))
        log_frame = ttk.Frame(bottom_frame)
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(log_frame, bg="#0d1117", fg="#8b949e",
                                font=("Consolas", 9), insertbackground="#e0e0e0",
                                relief="flat", wrap="word", height=4)
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        self.log_text.tag_configure("info", foreground="#58a6ff")
        self.log_text.tag_configure("success", foreground="#2ecc71")
        self.log_text.tag_configure("error", foreground="#f85149")
        self.log_text.tag_configure("warn", foreground="#f39c12")

        paned.add(bottom_frame, minsize=80)

    # ─── Tab 2: Local status ───────────────────────────────────────────────
    def _build_tab_local(self):
        tab = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(tab, text="  Local status  ")

        info_frame = ttk.LabelFrame(tab, text="current local Kiro Token", padding=15)
        info_frame.pack(fill="x", pady=(0, 10))

        self.status_text = tk.Text(info_frame, bg="#0d1117", fg="#e0e0e0",
                                   font=("Consolas", 10), relief="flat",
                                   wrap="word", height=12)
        self.status_text.pack(fill="both", expand=True)
        self.status_text.tag_configure("key", foreground="#00d2ff")
        self.status_text.tag_configure("val", foreground="#e0e0e0")
        self.status_text.tag_configure("ok", foreground="#2ecc71")
        self.status_text.tag_configure("expired", foreground="#f85149")

        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill="x", pady=10)
        ttk.Button(btn_frame, text="Refresh status",
                   command=self._refresh_local_status).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Refresh localToken", style="Green.TButton",
                   command=self._refresh_local_token).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Clear localToken", style="Red.TButton",
                   command=self._clear_local_token).pack(side="left", padx=5)

        path_frame = ttk.Frame(tab)
        path_frame.pack(fill="x", pady=(10, 0))
        ttk.Label(path_frame, text=f"Storage path: {KIRO_CACHE_DIR}",
                  style="Stats.TLabel").pack(anchor="w")

    # ─── Tab 3: Auto-register ─────────────────────────────────────────────────
    def _build_tab_register(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  Auto-register  ")

        # Options row
        opts_frame = ttk.Frame(tab)
        opts_frame.pack(fill="x", pady=(0, 8))

        self._reg_headless = tk.BooleanVar(value=False)
        self._reg_auto_login = tk.BooleanVar(value=True)
        self._reg_skip_onboard = tk.BooleanVar(value=True)
        self._reg_pro_trial = tk.BooleanVar(value=True)
        self._reg_import_no_trial = tk.BooleanVar(value=False)
        self._reg_use_roxy = tk.BooleanVar(value=False)
        self._use_9router_flow = tk.BooleanVar(value=False)
        self._reg_concurrency = tk.IntVar(value=1)

        ttk.Checkbutton(opts_frame, text="Headless", variable=self._reg_headless).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(opts_frame, text="Auto sign-in", variable=self._reg_auto_login).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(opts_frame, text="Skip onboarding", variable=self._reg_skip_onboard).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(opts_frame, text="ProTrial subscription", variable=self._reg_pro_trial).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(opts_frame, text="Persist even without a trial", variable=self._reg_import_no_trial).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(opts_frame, text="Fingerprint browser", variable=self._reg_use_roxy).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(opts_frame, text="Use 9router OAuth Flow", variable=self._use_9router_flow).pack(side="left", padx=(0, 12))

        # Batch automation config
        batch_frame = ttk.LabelFrame(tab, text="Batch Automation", padding=(8, 4))
        batch_frame.pack(fill="x", pady=(0, 8))

        batch_row1 = ttk.Frame(batch_frame)
        batch_row1.pack(fill="x", pady=2)
        ttk.Label(batch_row1, text="Batch count:", width=12).pack(side="left")
        self._reg_batch_count = tk.StringVar(value="10")
        ttk.Entry(batch_row1, textvariable=self._reg_batch_count, width=10).pack(side="left", padx=4)
        ttk.Label(batch_row1, text="Account delay (s):", width=15).pack(side="left", padx=(12, 0))
        self._reg_account_delay = tk.StringVar(value="10")
        ttk.Entry(batch_row1, textvariable=self._reg_account_delay, width=10).pack(side="left", padx=4)
        ttk.Label(batch_row1, text="Batch delay (s):", width=15).pack(side="left", padx=(12, 0))
        self._reg_batch_delay = tk.StringVar(value="60")
        ttk.Entry(batch_row1, textvariable=self._reg_batch_delay, width=10).pack(side="left", padx=4)

        batch_btn_frame = ttk.Frame(batch_frame)
        batch_btn_frame.pack(fill="x", pady=(4, 0))
        self._reg_auto_btn = ttk.Button(batch_btn_frame, text="Auto Register (Batch)", style="Green.TButton",
                                        command=self._reg_auto_batch)
        self._reg_auto_btn.pack(side="left", padx=(0, 5))
        self._reg_continuous_btn = ttk.Button(batch_btn_frame, text="Continuous Mode", style="Green.TButton",
                                              command=self._reg_continuous)
        self._reg_continuous_btn.pack(side="left", padx=(0, 5))

        btn_frame = ttk.Frame(opts_frame)
        btn_frame.pack(side="right")
        self._reg_start_btn = ttk.Button(btn_frame, text="Start registration", style="Green.TButton",
                                         command=self._reg_start)
        self._reg_start_btn.pack(side="left", padx=(0, 5))
        self._reg_pro_only_btn = ttk.Button(btn_frame, text="Subscribe onlyPro", style="Orange.TButton",
                                            command=self._reg_pro_only)
        self._reg_pro_only_btn.pack(side="left", padx=(0, 5))
        self._reg_stop_btn = ttk.Button(btn_frame, text="Stop", style="Red.TButton",
                                        command=self._reg_stop, state="disabled")
        self._reg_stop_btn.pack(side="left")

        # Mail service config
        mail_frame = ttk.LabelFrame(tab, text="Mail service config", padding=(8, 4))
        mail_frame.pack(fill="x", pady=(0, 8))

        cfg = load_config()

        # Service selection row
        row0 = ttk.Frame(mail_frame)
        row0.pack(fill="x", pady=2)
        ttk.Label(row0, text="Service:", width=8).pack(side="left")
        self._reg_mail_provider = tk.StringVar(value=cfg.get("mail_provider", "shiromail"))
        self._reg_provider_combo = ttk.Combobox(row0, textvariable=self._reg_mail_provider, width=16, state="readonly")
        from mail_providers import list_providers
        _providers = list_providers()
        self._reg_provider_combo["values"] = [p["display_name"] for p in _providers]
        self._reg_provider_name_map = {p["display_name"]: p["name"] for p in _providers}
        self._reg_provider_display_map = {p["name"]: p["display_name"] for p in _providers}
        # Set the currently displayed value
        current_provider = cfg.get("mail_provider", "shiromail")
        self._reg_mail_provider.set(self._reg_provider_display_map.get(current_provider, "ShiroMail"))
        self._reg_provider_combo.pack(side="left", padx=4)
        ttk.Label(row0, text="(Re-fill the configuration after switching)", foreground="#8b949e").pack(side="left", padx=4)

        row1 = ttk.Frame(mail_frame)
        row1.pack(fill="x", pady=2)
        ttk.Label(row1, text="API URL:", width=8).pack(side="left")
        self._reg_mail_url = tk.StringVar(value=cfg.get("mail_url", ""))
        ttk.Entry(row1, textvariable=self._reg_mail_url, width=45).pack(side="left", padx=(4, 12))
        ttk.Label(row1, text="Domain:").pack(side="left")
        self._reg_mail_domain_id = tk.StringVar(value=cfg.get("mail_domain_id", ""))
        self._reg_domain_combo = ttk.Combobox(row1, textvariable=self._reg_mail_domain_id, width=20, state="readonly")
        self._reg_domain_combo.pack(side="left", padx=4)
        self._reg_domain_map = {}
        ttk.Button(row1, text="Refresh", width=4, command=self._reg_refresh_domains).pack(side="left", padx=2)

        row2 = ttk.Frame(mail_frame)
        row2.pack(fill="x", pady=2)
        ttk.Label(row2, text="API Key:", width=8).pack(side="left")
        self._reg_mail_key = tk.StringVar(value=cfg.get("mail_key", ""))
        self._reg_mail_key_entry = ttk.Entry(row2, textvariable=self._reg_mail_key, width=45, show="*")
        self._reg_mail_key_entry.pack(side="left", padx=4)

        # Self-hosted catch-all IMAP rows (visible only when gsuite_imap is selected).
        self._imap_rows = []
        row_imap1 = ttk.Frame(mail_frame)
        ttk.Label(row_imap1, text="IMAP host:", width=10).pack(side="left")
        self._reg_imap_server = tk.StringVar(value=cfg.get("imap_server", "imap.gmail.com"))
        ttk.Entry(row_imap1, textvariable=self._reg_imap_server, width=28).pack(side="left", padx=4)
        ttk.Label(row_imap1, text="Port:").pack(side="left", padx=(8, 2))
        self._reg_imap_port = tk.StringVar(value=str(cfg.get("imap_port", 993)))
        ttk.Entry(row_imap1, textvariable=self._reg_imap_port, width=6).pack(side="left", padx=2)
        self._imap_rows.append(row_imap1)

        row_imap2 = ttk.Frame(mail_frame)
        ttk.Label(row_imap2, text="IMAP user:", width=10).pack(side="left")
        self._reg_imap_user = tk.StringVar(value=cfg.get("imap_user", ""))
        ttk.Entry(row_imap2, textvariable=self._reg_imap_user, width=45).pack(side="left", padx=4)
        self._imap_rows.append(row_imap2)

        row_imap3 = ttk.Frame(mail_frame)
        ttk.Label(row_imap3, text="IMAP pass:", width=10).pack(side="left")
        self._reg_imap_pass = tk.StringVar(value=cfg.get("imap_pass", ""))
        ttk.Entry(row_imap3, textvariable=self._reg_imap_pass, width=45, show="*").pack(side="left", padx=4)
        ttk.Label(row_imap3, text="(Gmail: use an app password)", foreground="#8b949e").pack(side="left", padx=4)
        self._imap_rows.append(row_imap3)

        row_imap4 = ttk.Frame(mail_frame)
        ttk.Label(row_imap4, text="Domains:", width=10).pack(side="left")
        default_domains_file = str((Path(getattr(sys, "_MEIPASS", ".")) / "domains.txt").resolve()) \
            if hasattr(sys, "_MEIPASS") else "domains.txt"
        self._reg_imap_domains_file = tk.StringVar(value=cfg.get("imap_domains_file", default_domains_file))
        ttk.Entry(row_imap4, textvariable=self._reg_imap_domains_file, width=45).pack(side="left", padx=4)
        ttk.Label(row_imap4, text="(path to domains.txt, one domain per line)",
                  foreground="#8b949e").pack(side="left", padx=4)
        self._imap_rows.append(row_imap4)

        def _on_provider_change(*_):
            display = self._reg_mail_provider.get()
            name = self._reg_provider_name_map.get(display, "shiromail")
            if name == "shiromail":
                self._reg_mail_key_entry.configure(state="normal")
                self._reg_domain_combo.configure(state="readonly")
            # Hide/show IMAP-only rows based on provider.
            show_imap = (name == "gsuite_imap")
            for r in self._imap_rows:
                if show_imap:
                    r.pack(fill="x", pady=2)
                else:
                    r.pack_forget()
        self._reg_mail_provider.trace_add("write", _on_provider_change)
        _on_provider_change()

        # EFunCard CDK config (Pro trial subscription))
        cdk_frame = ttk.LabelFrame(tab, text="EFunCard CDK (ProTrial subscription)", padding=(8, 4))
        cdk_frame.pack(fill="x", pady=(0, 8))

        cdk_row = ttk.Frame(cdk_frame)
        cdk_row.pack(fill="x", pady=2)
        ttk.Label(cdk_row, text="CDK code:", width=8).pack(side="left")
        self._reg_cdk_code = tk.StringVar(value=cfg.get("cdk_code", ""))
        ttk.Entry(cdk_row, textvariable=self._reg_cdk_code, width=45).pack(side="left", padx=4)
        ttk.Label(cdk_row, text="(format: US-XXXXX-XXXXX-XXXXX-XXXXX-XXXXX)", foreground="#8b949e").pack(side="left", padx=4)

        yc_row = ttk.Frame(cdk_frame)
        yc_row.pack(fill="x", pady=2)
        ttk.Label(yc_row, text="YesCaptcha:", width=8).pack(side="left")
        self._reg_yescaptcha_key = tk.StringVar(value=cfg.get("yescaptcha_key", ""))
        ttk.Entry(yc_row, textvariable=self._reg_yescaptcha_key, width=45).pack(side="left", padx=4)
        ttk.Label(yc_row, text="(API Key, Used to auto-solve hCaptcha)", foreground="#8b949e").pack(side="left", padx=4)

        # Multibot (multibot.cloud) — alternative captcha provider
        mb_row = ttk.Frame(cdk_frame)
        mb_row.pack(fill="x", pady=2)
        ttk.Label(mb_row, text="Multibot:", width=8).pack(side="left")
        self._reg_multibot_key = tk.StringVar(value=cfg.get("multibot_key", ""))
        ttk.Entry(mb_row, textvariable=self._reg_multibot_key, width=45).pack(side="left", padx=4)
        ttk.Label(mb_row, text="(API Key for multibot.cloud, alternative hCaptcha solver)", foreground="#8b949e").pack(side="left", padx=4)

        # Captcha provider selector
        cap_row = ttk.Frame(cdk_frame)
        cap_row.pack(fill="x", pady=2)
        ttk.Label(cap_row, text="Provider:", width=8).pack(side="left")
        self._reg_captcha_provider = tk.StringVar(value=cfg.get("captcha_provider", "yescaptcha"))
        ttk.Combobox(cap_row, textvariable=self._reg_captcha_provider,
                     values=["yescaptcha", "multibot"], state="readonly",
                     width=20).pack(side="left", padx=4)
        ttk.Label(cap_row, text="(Active hCaptcha solver)", foreground="#8b949e").pack(side="left", padx=4)

        # RoxyBrowser Fingerprint browser config
        roxy_row = ttk.Frame(cdk_frame)
        roxy_row.pack(fill="x", pady=2)
        ttk.Label(roxy_row, text="Roxy Key:", width=8).pack(side="left")
        self._reg_roxy_key = tk.StringVar(value=cfg.get("roxy_api_key", ""))
        ttk.Entry(roxy_row, textvariable=self._reg_roxy_key, width=45).pack(side="left", padx=4)
        ttk.Label(roxy_row, text="(RoxyBrowser API Key, Check'Fingerprint browser' hour(s) used)", foreground="#8b949e").pack(side="left", padx=4)

        # Proxy row (HTTP/SOCKS proxy URL with optional user:pass — applied to both
        # curl_cffi session and Playwright browser launch).
        proxy_row = ttk.Frame(mail_frame)
        proxy_row.pack(fill="x", pady=2)
        ttk.Label(proxy_row, text="Proxy URL:", width=18).pack(side="left")
        self._reg_proxy_url = tk.StringVar(value=cfg.get("proxy_url", ""))
        ttk.Entry(proxy_row, textvariable=self._reg_proxy_url, width=60).pack(side="left", padx=4)
        ttk.Label(proxy_row, text="(http://user:pass@host:port - leave blank for direct)",
                  foreground="#8b949e").pack(side="left", padx=4)

        # Persist automatically on value change
        def _save_mail_config(*_):
            domain_val = self._reg_mail_domain_id.get().strip()
            if hasattr(self, '_reg_domain_map') and domain_val in self._reg_domain_map:
                domain_val = self._reg_domain_map[domain_val]
            provider_display = self._reg_mail_provider.get()
            provider_name = self._reg_provider_name_map.get(provider_display, "shiromail")
            save_config({
                "mail_provider": provider_name,
                "mail_url": self._reg_mail_url.get().strip(),
                "mail_key": self._reg_mail_key.get().strip(),
                "mail_domain_id": domain_val,
                "imap_server": self._reg_imap_server.get().strip(),
                "imap_port": self._reg_imap_port.get().strip(),
                "imap_user": self._reg_imap_user.get().strip(),
                "imap_pass": self._reg_imap_pass.get().strip(),
                "imap_domains_file": self._reg_imap_domains_file.get().strip(),
                "cdk_code": self._reg_cdk_code.get().strip(),
                "yescaptcha_key": self._reg_yescaptcha_key.get().strip(),
                "multibot_key": self._reg_multibot_key.get().strip(),
                "captcha_provider": self._reg_captcha_provider.get().strip() or "yescaptcha",
                "roxy_api_key": self._reg_roxy_key.get().strip(),
                "proxy_url": self._reg_proxy_url.get().strip(),
                "auto_refresh_min": self._auto_refresh_min.get().strip(),
            })
        self._reg_mail_provider.trace_add("write", _save_mail_config)
        self._reg_mail_url.trace_add("write", _save_mail_config)
        self._reg_mail_key.trace_add("write", _save_mail_config)
        self._reg_mail_domain_id.trace_add("write", _save_mail_config)
        self._reg_imap_server.trace_add("write", _save_mail_config)
        self._reg_imap_port.trace_add("write", _save_mail_config)
        self._reg_imap_user.trace_add("write", _save_mail_config)
        self._reg_imap_pass.trace_add("write", _save_mail_config)
        self._reg_imap_domains_file.trace_add("write", _save_mail_config)
        self._reg_cdk_code.trace_add("write", _save_mail_config)
        self._reg_yescaptcha_key.trace_add("write", _save_mail_config)
        self._reg_multibot_key.trace_add("write", _save_mail_config)
        self._reg_captcha_provider.trace_add("write", _save_mail_config)
        self._reg_roxy_key.trace_add("write", _save_mail_config)
        self._reg_proxy_url.trace_add("write", _save_mail_config)

        # Terminal output
        term_frame = ttk.Frame(tab)
        term_frame.pack(fill="both", expand=True)

        self._reg_term = tk.Text(term_frame, bg="#0d1117", fg="#c9d1d9",
                                 font=("Consolas", 10), insertbackground="#c9d1d9",
                                 relief="flat", wrap="word")
        term_scroll = ttk.Scrollbar(term_frame, orient="vertical", command=self._reg_term.yview)
        self._reg_term.configure(yscrollcommand=term_scroll.set)
        self._reg_term.pack(side="left", fill="both", expand=True)
        term_scroll.pack(side="right", fill="y")

        self._reg_term.tag_configure("info", foreground="#58a6ff")
        self._reg_term.tag_configure("ok", foreground="#2ecc71")
        self._reg_term.tag_configure("err", foreground="#f85149")
        self._reg_term.tag_configure("dbg", foreground="#8b949e")
        self._reg_term.tag_configure("highlight", foreground="#f0e68c")

        # Queue for thread-safe log delivery
        self._reg_queue = queue.Queue()
        self._reg_running = False
        self._reg_cancel = False

    # ─── Tab 3 Actions: Auto-register ─────────────────────────────────────────
    def _reg_log(self, msg, level="info"):
        """Thread-safe log to registration terminal via queue."""
        self._reg_queue.put((msg, level))

    def _reg_poll_queue(self):
        """Poll the queue and write messages to the terminal widget."""
        has_msg = False
        try:
            while True:
                msg, level = self._reg_queue.get_nowait()
                has_msg = True
                ts = datetime.now().strftime("%H:%M:%S")
                prefix = {"info": "[*]", "ok": "[+]", "err": "[-]", "dbg": "[~]"}.get(level, "[?]")
                tag = level if level in ("info", "ok", "err", "dbg") else "info"
                self._reg_term.insert("end", f"{ts} {prefix} {msg}\n", tag)
                self._reg_term.see("end")
        except Exception:
            pass
        if self._reg_running or has_msg:
            self.after(100, self._reg_poll_queue)

    def _reg_start(self):
        """Start the registration process in a background thread."""
        try:
            self._reg_start_inner()
        except Exception as e:
            import traceback
            try:
                self._reg_term.delete("1.0", "end")
                self._reg_term.insert("end", f"Startup failed: {e}\n\n", "err")
                self._reg_term.insert("end", traceback.format_exc(), "dbg")
            except Exception:
                messagebox.showerror("Registration error", f"Startup failed: {e}\n\n{traceback.format_exc()}")

    def _reg_start_inner(self):
        if self._reg_running:
            return
        # Check dependencies
        missing = []
        dep_errors = []
        try:
            import curl_cffi  # noqa: F401
        except Exception as e:
            missing.append("curl_cffi")
            dep_errors.append(f"curl_cffi: {e}")
        try:
            import playwright  # noqa: F401
        except Exception as e:
            missing.append("playwright")
            dep_errors.append(f"playwright: {e}")
        try:
            import playwright_stealth  # noqa: F401
        except Exception as e:
            missing.append("playwright-stealth")
            dep_errors.append(f"playwright_stealth: {e}")
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        except Exception as e:
            missing.append("cryptography")
            dep_errors.append(f"cryptography: {e}")

        if missing:
            self._reg_term.delete("1.0", "end")
            self._reg_term.insert("end", "Missing dependency packages; install them first:\n\n", "err")
            cmd = f"pip install {' '.join(missing)}"
            self._reg_term.insert("end", f"  {cmd}\n\n", "highlight")
            if "playwright" in missing:
                self._reg_term.insert("end", "  playwright install chromium\n\n", "highlight")
            if dep_errors:
                self._reg_term.insert("end", "Detailed error:\n", "dbg")
                for err in dep_errors:
                    self._reg_term.insert("end", f"  {err}\n", "dbg")
            self._reg_term.insert("end", "\nNote: this feature requires running from a Python environment (python main.py),\n", "info")
            self._reg_term.insert("end", "PyInstaller The packaged exe does not include these dependencies.\n", "info")
            return

        # Check playwright browser is installed
        try:
            from playwright._impl._driver import compute_driver_executable
            driver_result = compute_driver_executable()
            # compute_driver_executable() returns either a string or a (node, cli) tuple
            # depending on the playwright version; normalise to the cli path.
            if isinstance(driver_result, tuple):
                driver_exec = driver_result[1]
            else:
                driver_exec = driver_result
            if not Path(driver_exec).exists():
                raise FileNotFoundError(driver_exec)
        except Exception:
            try:
                # Look for a downloaded browser binary in the platform-specific cache.
                # Playwright stores them in:
                #   macOS:   ~/Library/Caches/ms-playwright/
                #   Linux:   ~/.cache/ms-playwright/
                #   Windows: %USERPROFILE%/AppData/Local/ms-playwright/
                import sys as _sys
                candidates = []
                if _sys.platform == "darwin":
                    candidates.append(Path.home() / "Library" / "Caches" / "ms-playwright")
                elif _sys.platform.startswith("win"):
                    candidates.append(Path.home() / "AppData" / "Local" / "ms-playwright")
                else:
                    candidates.append(Path.home() / ".cache" / "ms-playwright")
                # Also look at the package-local fallback
                import playwright._impl._driver as _drv
                candidates.append(Path(_drv.__file__).parent.parent / "driver" / "package" / ".local-browsers")
                # Honour PLAYWRIGHT_BROWSERS_PATH env override
                import os as _os
                env_path = _os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
                if env_path:
                    candidates.insert(0, Path(env_path))
                # Look for any chromium-* directory in candidates
                browser_found = False
                for cand in candidates:
                    if cand.exists():
                        chromium_dirs = list(cand.glob("chromium-*"))
                        if chromium_dirs:
                            browser_found = True
                            break
                if not browser_found:
                    self._reg_term.delete("1.0", "end")
                    self._reg_term.insert("end", "Playwright Browser not installed; please run:\n\n", "err")
                    self._reg_term.insert("end", "  playwright install chromium\n", "highlight")
                    return
            except Exception:
                pass

        self._reg_running = True
        self._reg_cancel = False
        self._reg_term.delete("1.0", "end")
        self._reg_term.insert("end", f"{datetime.now().strftime('%H:%M:%S')} [*] Starting the registration flow...\n", "info")
        self._reg_start_btn.configure(state="disabled")
        self._reg_stop_btn.configure(state="normal")

        self.after(100, self._reg_poll_queue)

        headless = self._reg_headless.get()
        auto_login = self._reg_auto_login.get()
        skip_onboard = self._reg_skip_onboard.get()
        pro_trial = self._reg_pro_trial.get()
        import_no_trial = self._reg_import_no_trial.get()
        use_roxy = self._reg_use_roxy.get()

        MAX_RETRY = 5

        def _check_account_health(result):
            """Probe account health. Returns (status, reason)
            status: "ok" | "banned" | "no_trial"
            """
            access_token = result.get("accessToken", "")
            if not access_token:
                return "ok", ""
            profile_arn = FIXED_PROFILE_ARNS.get("BuilderId", "")
            # 1) Refresh the token to verify the account is alive
            client_id = result.get("clientId", "")
            client_secret = result.get("clientSecret", "")
            refresh_token = result.get("refreshToken", "")
            if client_id and client_secret and refresh_token:
                refreshed = refresh_idc_token(client_id, client_secret, refresh_token)
                if refreshed is None:
                    return "banned", "token Refresh failed (account is banned))"
                access_token = refreshed["accessToken"]
            # 2) Query usage to detect a ban
            usage = query_usage(access_token, profile_arn)
            if not usage["ok"]:
                err_data = usage.get("error", {})
                err_str = str(err_data)
                if "TEMPORARILY_SUSPENDED" in err_str:
                    return "banned", "Account is temporarily banned (TEMPORARILY_SUSPENDED)"
                if usage.get("status") == 403:
                    return "banned", f"Access denied (HTTP 403)"
            # 3) Check whether the free trial is available (API pre-check))
            if pro_trial:
                import kiro_subscribe
                subs = kiro_subscribe.list_available_subscriptions(access_token, profile_arn, log=self._reg_log)
                if subs.get("ok"):
                    plans = subs.get("plans", [])
                    pro_plan = None
                    for p in plans:
                        qt = p.get("qSubscriptionType", "")
                        if "PRO" in qt.upper() and "PLUS" not in qt.upper():
                            pro_plan = p
                            break
                    if not pro_plan and plans:
                        pro_plan = plans[0]
                    if pro_plan:
                        pricing = pro_plan.get("pricing", {})
                        amount = pricing.get("amount")
                        # amount == 0 indicates a trial is available; amount > 0 indicates no trial; None/-1 indicates unknown (continue)
                        if amount is not None and amount != -1 and float(amount) > 0:
                            return "no_trial", f"Account has no free trial (due today: ${float(amount):.2f} {pricing.get('currency', 'USD')})"
                        elif amount is not None and float(amount) == 0:
                            self._reg_log(f"API Confirmed: $0 Trial eligibility", "ok")
            return "ok", ""

        def _do_import_and_subscribe(result, loop):
            """Import + Subscription flow"""
            import random
            try:
                self._reg_import_to_db(result)
                self._reg_queue.put((f"✓ Account imported into database: {result['email']}", "ok"))
                self._refresh_after_import(result.get("email", ""), self._reg_queue)

                # Auto-export to 9router if enabled
                cfg = load_config()
                if cfg.get("enable_9router_export", False):
                    try:
                        from router9_export import Router9Exporter
                        exporter = Router9Exporter(
                            base_url=cfg.get("router9_url", "http://localhost:20128"),
                            verify_ssl=cfg.get("router9_verify_ssl", True)
                        )
                        if exporter.check_connection():
                            export_result = exporter.export_account_result(result, log=self._reg_log)
                            if export_result["ok"]:
                                self._reg_queue.put((f"✓ Account exported to 9router: {result['email']}", "ok"))
                            else:
                                self._reg_queue.put((f"9router export failed: {export_result.get('error', 'unknown')}", "warn"))
                        else:
                            self._reg_queue.put(("9router not accessible; skipping export", "warn"))
                    except ImportError:
                        self._reg_queue.put(("router9_export.py not found; skipping 9router export", "warn"))
                    except Exception as e:
                        self._reg_queue.put((f"9router export error: {e}", "err"))
            except Exception as e:
                self._reg_queue.put((f"Database import failed: {e}", "err"))
            self.after(0, self._load_accounts_from_db)

            if pro_trial and result.get("accessToken"):
                self._reg_queue.put(("", "info"))
                warmup = random.randint(30, 90)
                self._reg_queue.put((f"Warm-up delay: waiting {warmup}s to simulate natural user behavior before subscription", "info"))
                time.sleep(warmup)
                self._reg_queue.put((f"Starting Pro trial subscription for {result['email']}...", "info"))
                try:
                    loop.run_until_complete(
                        self._reg_pro_trial_subscribe(result, loop)
                    )
                except Exception as e:
                    err_str = str(e)
                    if "closed" in err_str.lower():
                        self._reg_queue.put(("Payment page closed unexpectedly", "err"))
                    elif "timeout" in err_str.lower():
                        self._reg_queue.put(("Payment operation timed out", "err"))
                    else:
                        self._reg_queue.put((f"Pro Trial subscription failed: {err_str[:80]}", "err"))
            elif pro_trial and not result.get("accessToken"):
                self._reg_queue.put(("No token; skipping Pro subscription", "warn"))

        def _worker():
            import traceback as _tb
            import random
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                for attempt in range(1, MAX_RETRY + 1):
                    if self._reg_cancel:
                        self._reg_queue.put(("Registration cancelled by user; stopping retry loop", "warn"))
                        break
                    if attempt > 1:
                        wait = random.randint(10, 30)
                        self._reg_queue.put((f"Retry delay: waiting {wait}s before attempt [{attempt}/{MAX_RETRY}]", "info"))
                        time.sleep(wait)
                    self._reg_queue.put((f"[{attempt}/{MAX_RETRY}] Starting registration attempt (headless={headless}, concurrency={self._reg_concurrency.get()})", "info"))
                    try:
                        result = loop.run_until_complete(
                            self._reg_async_main(headless, auto_login, skip_onboard, use_roxy=use_roxy)
                        )
                    except Exception as e:
                        self._reg_queue.put((f"Registration error: {e}", "err"))
                        self._reg_queue.put((_tb.format_exc(), "dbg"))
                        continue

                    if not result or not result.get("email"):
                        self._reg_queue.put((f"[{attempt}/{MAX_RETRY}] Registration flow returned no result; retrying", "err"))
                        continue

                    self._reg_queue.put((f"[{attempt}/{MAX_RETRY}] ✓ Registration successful: {result['email']}", "ok"))
                    self._reg_queue.put((f"  Email: {result['email']}", "highlight"))
                    self._reg_queue.put((f"  Password: {result['password']}", "highlight"))

                    # Account health check
                    self._reg_queue.put(("Verifying account health (checking for bans/trial status)...", "info"))
                    status, reason = _check_account_health(result)

                    if status == "banned":
                        self._reg_queue.put((f"[-] {reason}, skip-DB", "err"))
                        if attempt < MAX_RETRY:
                            self._reg_queue.put(("Account banned; will auto-register a replacement...", "warn"))
                        continue

                    if status == "no_trial":
                        self._reg_queue.put((f"[-] {reason}", "warn"))
                        if import_no_trial:
                            self._reg_queue.put(("No trial available, but the user opted to import anyway; persisting now+Subscription...", "info"))
                            _do_import_and_subscribe(result, loop)
                        else:
                            self._reg_queue.put(("Skip persisting (selectable)'Persist even without a trial'change this behaviour)", "info"))
                        if attempt < MAX_RETRY:
                            self._reg_queue.put(("A new account will be registered automatically to obtain a trial...", "warn"))
                        continue

                    # Account healthy
                    is_incomplete = result.get("incomplete", False)
                    if is_incomplete:
                        fail_reason = result.get('failReason', '')
                        self._reg_queue.put((f"[-] Registration incomplete ({fail_reason}), skip-DB", "err"))

                        if attempt < MAX_RETRY:
                            # TES blocks need longer cooling time
                            is_tes_block = "TES" in fail_reason or "Blocked" in fail_reason
                            if is_tes_block:
                                retry_delay = 15 + (attempt * 10)  # 15s, 25s, 35s
                                self._reg_queue.put((f"⚠ AWS TES block detected. Cooling down {retry_delay}s before retry [{attempt + 1}/{MAX_RETRY}]", "warn"))
                            else:
                                retry_delay = 5 + (attempt * 3)  # 5s, 8s, 11s
                                self._reg_queue.put((f"Non-TES failure. Waiting {retry_delay}s before retry [{attempt + 1}/{MAX_RETRY}]", "warn"))

                            # Sleep with cancel check
                            for _ in range(retry_delay * 10):
                                if self._reg_cancel:
                                    break
                                time.sleep(0.1)

                            if not self._reg_cancel:
                                self._reg_queue.put(("Will auto-register a replacement...", "warn"))
                        continue
                    self._reg_queue.put(("Registration complete! Account is healthy", "ok"))
                    _do_import_and_subscribe(result, loop)
                    break
                else:
                    self._reg_queue.put((f"⛔ Maximum retry limit reached ({MAX_RETRY} attempts exhausted). Registration failed.", "err"))
                loop.close()
            except Exception as e:
                self._reg_queue.put((f"Registration error: {e}", "err"))
                try:
                    self._reg_queue.put((_tb.format_exc(), "dbg"))
                except Exception:
                    pass
            finally:
                self._reg_running = False
                self.after(0, lambda: self._reg_start_btn.configure(state="normal"))
                self.after(0, lambda: self._reg_stop_btn.configure(state="disabled"))

        threading.Thread(target=_worker, daemon=True).start()

    def _reg_refresh_domains(self):
        """Fetching available domains from the mail service"""
        from mail_providers import get_provider
        base_url = self._reg_mail_url.get().strip().rstrip("/")
        api_key = self._reg_mail_key.get().strip()
        provider_display = self._reg_mail_provider.get()
        provider_name = self._reg_provider_name_map.get(provider_display, "shiromail")
        if not base_url:
            from tkinter import messagebox
            messagebox.showwarning("Info", "Please fill in API URL")
            return
        if provider_name == "shiromail" and not api_key:
            from tkinter import messagebox
            messagebox.showwarning("Info", "Please fill in API Key")
            return
        try:
            kwargs = {"base_url": base_url}
            if provider_name == "shiromail":
                kwargs["api_key"] = api_key
            provider = get_provider(provider_name, **kwargs)
            domains = provider.list_domains()
            if domains:
                self._reg_domain_map = {}
                display_list = []
                for d in domains:
                    domain_name = d.get("domain", "")
                    domain_id = d.get("id", "")
                    if domain_name and domain_id:
                        self._reg_domain_map[domain_name] = domain_id
                        display_list.append(domain_name)
                self._reg_domain_combo["values"] = display_list
                current = self._reg_mail_domain_id.get().strip()
                if current and current in [v for v in self._reg_domain_map.values()]:
                    for name, did in self._reg_domain_map.items():
                        if did == current:
                            self._reg_mail_domain_id.set(name)
                            break
                elif display_list and not current:
                    self._reg_mail_domain_id.set(display_list[0])
                self._reg_domain_combo.configure(state="readonly")
            else:
                from tkinter import messagebox
                messagebox.showinfo("Info", "No domain list returned")
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("Error", f"Request failed: {e}")

    def _reg_stop(self):
        """Signal the registration to abort."""
        self._reg_cancel = True
        self._reg_log("⏹ Stop requested by user; cancelling active registration operations...", "warn")

    def _reg_pro_only(self):
        """Skip registration; run the Pro trial subscription against the most recent account already in the database"""
        if self._reg_running:
            return
        # Pulling the latest account from the database
        rows = db_get_all(self.conn)
        if not rows:
            from tkinter import messagebox
            messagebox.showwarning("Info", "Database is empty; register an account first")
            return
        # Take the latest entry (the most recently registered)
        row = rows[-1]
        access_token, err = get_valid_token(row, self.conn)
        if not access_token:
            from tkinter import messagebox
            messagebox.showerror("Error", f"Token invalid: {err}\nRefresh or re-register first")
            return

        self._reg_running = True
        self._reg_cancel = False
        self._reg_term.delete("1.0", "end")
        self._reg_term.insert("end", f"{datetime.now().strftime('%H:%M:%S')} [*] PRO-ONLY MODE: Subscribing existing account to Pro trial (no registration)\n", "info")
        self._reg_term.insert("end", f"  Target account: {row['email']}\n", "info")
        self._reg_start_btn.configure(state="disabled")
        self._reg_pro_only_btn.configure(state="disabled")
        self._reg_stop_btn.configure(state="normal")
        self.after(100, self._reg_poll_queue)

        result = {
            "email": row["email"],
            "accessToken": access_token,
            "provider": row["provider"] or "BuilderId",
        }

        def _worker():
            try:
                import traceback as _tb
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._reg_queue.put(("Starting the Pro trial subscription...", "info"))
                loop.run_until_complete(self._reg_pro_trial_subscribe(result, loop))
                loop.close()
            except Exception as e:
                err_str = str(e)
                if "closed" in err_str.lower():
                    self._reg_queue.put(("The payment page closed unexpectedly; please retry", "err"))
                elif "timeout" in err_str.lower():
                    self._reg_queue.put(("Payment operation timed out; check the network and retry", "err"))
                else:
                    self._reg_queue.put((f"Pro Trial subscription failed: {err_str[:80]}", "err"))
            finally:
                self._reg_running = False
                self.after(0, lambda: self._reg_start_btn.configure(state="normal"))
                self.after(0, lambda: self._reg_pro_only_btn.configure(state="normal"))
                self.after(0, lambda: self._reg_stop_btn.configure(state="disabled"))

        threading.Thread(target=_worker, daemon=True).start()

    def _reg_auto_batch(self):
        """Auto register N accounts with delay between each."""
        if self._reg_running:
            return

        try:
            count = int(self._reg_batch_count.get())
            delay = int(self._reg_account_delay.get())
        except ValueError:
            messagebox.showerror("Error", "Batch count and account delay must be integers")
            return

        if count <= 0:
            messagebox.showerror("Error", "Batch count must be > 0")
            return

        self._reg_running = True
        self._reg_cancel = False
        self._reg_term.delete("1.0", "end")
        self._reg_term.insert("end", f"[*] BATCH MODE STARTED: {count} accounts, {delay}s delay between accounts, max 3 retries per account\n", "info")
        self._reg_start_btn.configure(state="disabled")
        self._reg_auto_btn.configure(state="disabled")
        self._reg_continuous_btn.configure(state="disabled")
        self._reg_pro_only_btn.configure(state="disabled")
        self._reg_stop_btn.configure(state="normal")
        self.after(100, self._reg_poll_queue)

        def _worker():
            successful = 0
            failed = 0

            for i in range(1, count + 1):
                if self._reg_cancel:
                    self._reg_queue.put((f"Batch stopped by user at account {i-1}/{count}", "warn"))
                    break

                self._reg_queue.put((f"\n{'='*50}", "info"))
                self._reg_queue.put((f"[Account {i}/{count}] Starting registration attempt...", "info"))

                # Cache for retry persistence (per account scope)
                cached_email = None
                cached_device_code_data = None

                # Retry loop per account
                MAX_RETRY = 3
                account_success = False

                for attempt in range(1, MAX_RETRY + 1):
                    if self._reg_cancel:
                        break

                    if attempt > 1:
                        self._reg_queue.put((f"  [Account {i}/{count}] Retry attempt [{attempt}/{MAX_RETRY}]", "info"))

                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)

                        # Get registration options
                        headless = self._reg_headless.get()
                        auto_login = self._reg_auto_login.get()
                        skip_onboard = self._reg_skip_onboard.get()
                        use_roxy = self._reg_use_roxy.get()

                        # Log cache status
                        if cached_email:
                            self._reg_queue.put((f"  Reusing cached email: {cached_email}", "info"))
                        if cached_device_code_data:
                            expires_at = cached_device_code_data.get("expires_at", 0)
                            remaining = max(0, expires_at - time.time())
                            self._reg_queue.put((f"  Reusing cached device code (expires in {int(remaining)}s)", "info"))

                        result = loop.run_until_complete(
                            self._reg_async_main(headless, auto_login, skip_onboard, use_roxy=use_roxy,
                                                cached_email=cached_email, cached_device_code_data=cached_device_code_data)
                        )

                        if result and result.get("email"):
                            # Cache email AND device code for next retry
                            cached_email = result.get("email")
                            cached_device_code_data = result.get("device_code_info")

                            if cached_email:
                                self._reg_queue.put((f"ℹ️  Email cached: {cached_email}", "info"))

                            if cached_device_code_data:
                                expires_at_timestamp = cached_device_code_data.get("expires_at_timestamp", 0)
                                remaining = max(0, int(expires_at_timestamp - time.time()))
                                self._reg_queue.put((f"ℹ️  Device code cached (expires in {remaining}s)", "info"))

                                # Check if expired - clear cache if so
                                if remaining <= 0:
                                    self._reg_queue.put(("⚠️  Device code expired, clearing cache", "warn"))
                                    cached_email = None
                                    cached_device_code_data = None

                            # Check if incomplete (TES block, export failure, etc.)
                            is_incomplete = result.get("incomplete", False)
                            if is_incomplete:
                                fail_reason = result.get("failReason", "Unknown")
                                self._reg_queue.put((f"❌ Registration incomplete: {fail_reason}", "err"))

                                if attempt < MAX_RETRY:
                                    # TES blocks need longer cooling
                                    is_tes_block = "TES" in fail_reason or "Blocked" in fail_reason
                                    retry_delay = (15 + (attempt * 10)) if is_tes_block else (5 + (attempt * 3))
                                    self._reg_queue.put((f"  Waiting {retry_delay}s before retry...", "warn"))

                                    for _ in range(retry_delay * 10):
                                        if self._reg_cancel:
                                            break
                                        time.sleep(0.1)
                                continue

                            # Complete and healthy - import to DB
                            successful += 1
                            account_success = True
                            self._reg_queue.put((f"✅ Success! Email: {result['email']}", "ok"))

                            # Import to database and subscribe if enabled
                            try:
                                self._reg_import_to_db(result)
                                self._reg_queue.put(("  Imported to database", "ok"))

                                # Auto-subscribe to Pro if enabled
                                if self._reg_pro_trial.get():
                                    self._reg_queue.put(("  Starting Pro trial subscription...", "info"))
                                    loop.run_until_complete(self._reg_pro_trial_subscribe(result, loop))
                            except Exception as e:
                                self._reg_queue.put((f"  DB import/subscribe error: {e}", "warn"))

                            break
                        else:
                            # Other failure
                            error = result.get("error", "Unknown") if result else "No result"
                            self._reg_queue.put((f"❌ Failed: {error}", "err"))
                            if attempt < MAX_RETRY:
                                self._reg_queue.put((f"  Waiting 5s before retry...", "warn"))
                                for _ in range(50):
                                    if self._reg_cancel:
                                        break
                                    time.sleep(0.1)
                            continue

                    except Exception as e:
                        self._reg_queue.put((f"❌ Error: {e}", "err"))
                        if attempt < MAX_RETRY:
                            self._reg_queue.put((f"  Waiting 5s before retry...", "warn"))
                            for _ in range(50):
                                if self._reg_cancel:
                                    break
                                time.sleep(0.1)

                if not account_success:
                    failed += 1

                if i < count and not self._reg_cancel:
                    self._reg_queue.put((f"⏳ Account delay: waiting {delay}s before next registration [{i+1}/{count}]", "info"))
                    for _ in range(delay * 10):
                        if self._reg_cancel:
                            break
                        time.sleep(0.1)

            self._reg_queue.put((f"\n{'='*50}", "info"))
            self._reg_queue.put((f"BATCH MODE COMPLETED", "info"))
            self._reg_queue.put((f"  Total accounts attempted: {count}", "info"))
            self._reg_queue.put((f"  ✓ Successful: {successful}", "ok"))
            self._reg_queue.put((f"  ✗ Failed: {failed}", "err"))
            self._reg_queue.put((f"{'='*50}", "info"))

            self._reg_running = False
            self.after(0, lambda: self._reg_start_btn.configure(state="normal"))
            self.after(0, lambda: self._reg_auto_btn.configure(state="normal"))
            self.after(0, lambda: self._reg_continuous_btn.configure(state="normal"))
            self.after(0, lambda: self._reg_pro_only_btn.configure(state="normal"))
            self.after(0, lambda: self._reg_stop_btn.configure(state="disabled"))

        threading.Thread(target=_worker, daemon=True).start()

    def _reg_continuous(self):
        """Continuous registration mode - infinite loop with batch delays."""
        if self._reg_running:
            return

        try:
            batch_count = int(self._reg_batch_count.get())
            account_delay = int(self._reg_account_delay.get())
            batch_delay = int(self._reg_batch_delay.get())
        except ValueError:
            messagebox.showerror("Error", "All delay values must be integers")
            return

        if batch_count <= 0:
            messagebox.showerror("Error", "Batch count must be > 0")
            return

        self._reg_running = True
        self._reg_cancel = False
        self._reg_term.delete("1.0", "end")
        self._reg_term.insert("end", f"[*] CONTINUOUS MODE STARTED (runs until stopped)\n", "info")
        self._reg_term.insert("end", f"    Accounts per batch: {batch_count}\n", "info")
        self._reg_term.insert("end", f"    Account delay: {account_delay}s\n", "info")
        self._reg_term.insert("end", f"    Batch delay: {batch_delay}s\n", "info")
        self._reg_term.insert("end", f"    Max retries per account: 3\n", "info")
        self._reg_term.insert("end", f"    Stop: Press 'Stop' button\n", "info")
        self._reg_start_btn.configure(state="disabled")
        self._reg_auto_btn.configure(state="disabled")
        self._reg_continuous_btn.configure(state="disabled")
        self._reg_pro_only_btn.configure(state="disabled")
        self._reg_stop_btn.configure(state="normal")
        self.after(100, self._reg_poll_queue)

        def _worker():
            batch_num = 0
            total_successful = 0
            total_failed = 0

            while not self._reg_cancel:
                batch_num += 1
                batch_successful = 0
                batch_failed = 0

                self._reg_queue.put((f"\n{'='*50}", "info"))
                self._reg_queue.put((f"BATCH #{batch_num} STARTED", "info"))
                self._reg_queue.put((f"{'='*50}", "info"))

                for i in range(1, batch_count + 1):
                    if self._reg_cancel:
                        break

                    self._reg_queue.put((f"\n[Batch #{batch_num}, Account {i}/{batch_count}] Starting registration...", "info"))

                    # Cache for retry persistence (per account scope)
                    cached_email = None
                    cached_device_code_data = None

                    # Retry loop per account
                    MAX_RETRY = 3
                    account_success = False

                    for attempt in range(1, MAX_RETRY + 1):
                        if self._reg_cancel:
                            break

                        if attempt > 1:
                            self._reg_queue.put((f"  [Batch #{batch_num}, Account {i}/{batch_count}] Retry [{attempt}/{MAX_RETRY}]", "info"))

                        try:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)

                            # Get registration options
                            headless = self._reg_headless.get()
                            auto_login = self._reg_auto_login.get()
                            skip_onboard = self._reg_skip_onboard.get()
                            use_roxy = self._reg_use_roxy.get()

                            # Log cache status
                            if cached_email:
                                self._reg_queue.put((f"  Reusing cached email: {cached_email}", "info"))
                            if cached_device_code_data:
                                expires_at = cached_device_code_data.get("expires_at", 0)
                                remaining = max(0, expires_at - time.time())
                                self._reg_queue.put((f"  Reusing cached device code (expires in {int(remaining)}s)", "info"))

                            result = loop.run_until_complete(
                                self._reg_async_main(headless, auto_login, skip_onboard, use_roxy=use_roxy,
                                                    cached_email=cached_email, cached_device_code_data=cached_device_code_data)
                            )

                            if result and result.get("email"):
                                # Cache email AND device code for next retry
                                cached_email = result.get("email")
                                cached_device_code_data = result.get("device_code_info")

                                if cached_email:
                                    self._reg_queue.put((f"ℹ️  Email cached: {cached_email}", "info"))

                                if cached_device_code_data:
                                    expires_at_timestamp = cached_device_code_data.get("expires_at_timestamp", 0)
                                    remaining = max(0, int(expires_at_timestamp - time.time()))
                                    self._reg_queue.put((f"ℹ️  Device code cached (expires in {remaining}s)", "info"))

                                    # Check if expired - clear cache if so
                                    if remaining <= 0:
                                        self._reg_queue.put(("⚠️  Device code expired, clearing cache", "warn"))
                                        cached_email = None
                                        cached_device_code_data = None

                                # Check if incomplete (TES block, export failure, etc.)
                                is_incomplete = result.get("incomplete", False)
                                if is_incomplete:
                                    fail_reason = result.get("failReason", "Unknown")
                                    self._reg_queue.put((f"❌ Registration incomplete: {fail_reason}", "err"))

                                    if attempt < MAX_RETRY:
                                        # TES blocks need longer cooling
                                        is_tes_block = "TES" in fail_reason or "Blocked" in fail_reason
                                        retry_delay = (15 + (attempt * 10)) if is_tes_block else (5 + (attempt * 3))
                                        delay_reason = "TES block cooldown" if is_tes_block else "standard retry delay"
                                        self._reg_queue.put((f"  ⚠ {delay_reason}: waiting {retry_delay}s before retry [{attempt + 1}/{MAX_RETRY}]", "warn"))

                                        for _ in range(retry_delay * 10):
                                            if self._reg_cancel:
                                                break
                                            time.sleep(0.1)
                                    continue

                                # Complete and healthy - import to DB
                                batch_successful += 1
                                total_successful += 1
                                account_success = True
                                self._reg_queue.put((f"✅ Success! Email: {result['email']}", "ok"))

                                # Import to database and subscribe if enabled
                                try:
                                    self._reg_import_to_db(result)
                                    self._reg_queue.put(("  Imported to database", "ok"))

                                    # Auto-subscribe to Pro if enabled
                                    if self._reg_pro_trial.get():
                                        self._reg_queue.put(("  Starting Pro trial subscription...", "info"))
                                        loop.run_until_complete(self._reg_pro_trial_subscribe(result, loop))
                                except Exception as e:
                                    self._reg_queue.put((f"  DB import/subscribe error: {e}", "warn"))

                                break
                            else:
                                # Other failure
                                error = result.get("error", "Unknown") if result else "No result"
                                self._reg_queue.put((f"❌ Failed: {error}", "err"))
                                if attempt < MAX_RETRY:
                                    self._reg_queue.put((f"  Waiting 5s before retry...", "warn"))
                                    for _ in range(50):
                                        if self._reg_cancel:
                                            break
                                        time.sleep(0.1)
                                continue

                        except Exception as e:
                            self._reg_queue.put((f"❌ Error: {e}", "err"))
                            if attempt < MAX_RETRY:
                                self._reg_queue.put((f"  Waiting 5s before retry...", "warn"))
                                for _ in range(50):
                                    if self._reg_cancel:
                                        break
                                    time.sleep(0.1)

                    if not account_success:
                        batch_failed += 1
                        total_failed += 1

                    if i < batch_count and not self._reg_cancel:
                        self._reg_queue.put((f"⏳ Account delay {account_delay}s...", "info"))
                        for _ in range(account_delay * 10):
                            if self._reg_cancel:
                                break
                            time.sleep(0.1)

                self._reg_queue.put((f"\nBatch #{batch_num} completed: ✅ {batch_successful} successful | ❌ {batch_failed} failed", "info"))
                self._reg_queue.put((f"Cumulative totals: ✅ {total_successful} successful | ❌ {total_failed} failed", "info"))

                if self._reg_cancel:
                    break

                self._reg_queue.put((f"\n⏳ Batch delay: waiting {batch_delay}s before starting batch #{batch_num + 1}", "info"))
                for _ in range(batch_delay * 10):
                    if self._reg_cancel:
                        break
                    time.sleep(0.1)

            self._reg_queue.put((f"\n{'='*50}", "info"))
            self._reg_queue.put((f"CONTINUOUS MODE STOPPED", "warn"))
            self._reg_queue.put((f"  Total batches completed: {batch_num}", "info"))
            self._reg_queue.put((f"  ✓ Total successful: {total_successful}", "ok"))
            self._reg_queue.put((f"  ✗ Total failed: {total_failed}", "err"))
            self._reg_queue.put((f"{'='*50}", "info"))

            self._reg_running = False
            self.after(0, lambda: self._reg_start_btn.configure(state="normal"))
            self.after(0, lambda: self._reg_auto_btn.configure(state="normal"))
            self.after(0, lambda: self._reg_continuous_btn.configure(state="normal"))
            self.after(0, lambda: self._reg_pro_only_btn.configure(state="normal"))
            self.after(0, lambda: self._reg_stop_btn.configure(state="disabled"))

        threading.Thread(target=_worker, daemon=True).start()

    def _reg_import_to_db(self, result):
        """Import a successful registration result into the SQLite DB."""
        account_data = {
            "email": result["email"],
            "password": result.get("password", ""),
            "provider": result.get("provider", "BuilderId"),
            "authMethod": result.get("authMethod", "IdC"),
            "accessToken": result.get("accessToken", ""),
            "refreshToken": result.get("refreshToken", ""),
            "expiresAt": result.get("expiresAt", ""),
            "clientId": result.get("clientId", ""),
            "clientSecret": result.get("clientSecret", ""),
            "clientIdHash": result.get("clientIdHash", ""),
            "region": result.get("region", "us-east-1"),
            "profileArn": FIXED_PROFILE_ARNS.get("BuilderId", ""),
            "userId": "",
            "usageLimit": 0,
            "currentUsage": 0,
            "overageCap": 0,
            "currentOverages": 0,
            "overageStatus": "",
            "overageCharges": 0.0,
            "subscription": "",
            "lastQueryTime": None,
        }
        db_upsert_account(self.conn, account_data)

    async def _reg_async_main(self, headless=True, auto_login=True, skip_onboard=True, use_roxy=False, cached_email=None, cached_device_code_data=None):
        """Drives the full registration flow via the kiro_register or roxy_register module"""
        from mail_providers import get_provider
        mail_url = self._reg_mail_url.get().strip() or None
        mail_key = self._reg_mail_key.get().strip() or None
        mail_domain_val = self._reg_mail_domain_id.get().strip() or None
        if mail_domain_val and hasattr(self, '_reg_domain_map') and mail_domain_val in self._reg_domain_map:
            mail_domain_id = self._reg_domain_map[mail_domain_val]
        else:
            mail_domain_id = mail_domain_val
        # Build the provider instance based on the selected service
        provider_display = self._reg_mail_provider.get()
        provider_name = self._reg_provider_name_map.get(provider_display, "shiromail")
        if provider_name == "gsuite_imap":
            # Self-hosted catch-all: IMAP credentials + domain pool from domains.txt.
            domains_file = (self._reg_imap_domains_file.get().strip()
                            or str(Path(__file__).resolve().parent / "domains.txt"))
            try:
                imap_port_int = int(self._reg_imap_port.get().strip() or 993)
            except ValueError:
                imap_port_int = 993
            provider_kwargs = {
                "imap_server": self._reg_imap_server.get().strip() or "imap.gmail.com",
                "imap_port": imap_port_int,
                "imap_user": self._reg_imap_user.get().strip(),
                "imap_pass": self._reg_imap_pass.get().strip(),
                "domains_file": domains_file,
            }
        else:
            provider_kwargs = {"base_url": mail_url or ""}
            if provider_name == "shiromail":
                provider_kwargs["api_key"] = mail_key or ""
                provider_kwargs["domain_id"] = mail_domain_id
        mail_instance = get_provider(provider_name, **provider_kwargs)

        proxy_url = self._reg_proxy_url.get().strip() if hasattr(self, "_reg_proxy_url") else ""

        if use_roxy:
            from roxy_register import register_with_roxy
            roxy_key = self._reg_roxy_key.get().strip()
            if not roxy_key:
                self._reg_log("Not configured RoxyBrowser API Key!", "err")
                return None
            return await register_with_roxy(
                api_key=roxy_key,
                headless=headless,
                auto_login=auto_login,
                skip_onboard=skip_onboard,
                mail_provider_instance=mail_instance,
                log=self._reg_log,
                cancel_check=lambda: self._reg_cancel,
            )
        else:
            import kiro_register

            # Check if 9router OAuth flow is enabled
            if self._use_9router_flow.get():
                cfg = load_config()
                router9_config = {
                    "base_url": cfg.get("router9_url", "http://localhost:20128"),
                    "password": cfg.get("router9_password", ""),
                    "auth_token": cfg.get("router9_auth_token", ""),
                    "auth_token_expires_at": cfg.get("router9_auth_token_expires_at", ""),
                }

                if not router9_config["password"]:
                    self._reg_log("9router password not configured in kiro_config.json", "err")
                    return None

                result = await kiro_register.register_via_9router_oauth(
                    headless=headless,
                    auto_login=auto_login,
                    skip_onboard=skip_onboard,
                    mail_provider_instance=mail_instance,
                    router9_config=router9_config,
                    proxy_url=proxy_url,
                    log=self._reg_log,
                    cancel_check=lambda: self._reg_cancel,
                    cached_email=cached_email,
                    cached_device_code_data=cached_device_code_data
                )

                # Update cached auth token in config if refreshed
                if result and router9_config.get("auth_token"):
                    cfg["router9_auth_token"] = router9_config["auth_token"]
                    cfg["router9_auth_token_expires_at"] = router9_config["auth_token_expires_at"]
                    save_config(cfg)

                return result
            else:
                # Original registration flow
                return await kiro_register.register(
                    headless=headless,
                    auto_login=auto_login,
                    skip_onboard=skip_onboard,
                    mail_provider_instance=mail_instance,
                    proxy_url=proxy_url,
                    log=self._reg_log,
                    cancel_check=lambda: self._reg_cancel,
                )

    async def _reg_pro_trial_subscribe(self, result, loop):
        """After registration, automatically subscribe to the Pro trial (using an EFunCard virtual credit card)"""
        import kiro_subscribe
        import os as _os
        from stripe_pay import auto_pay

        # Ensure the captcha solver is wired up.
        cfg = load_config()
        yescaptcha_key = self._reg_yescaptcha_key.get().strip()
        multibot_key = self._reg_multibot_key.get().strip()
        captcha_provider = (self._reg_captcha_provider.get().strip() or "yescaptcha")
        if captcha_provider == "multibot":
            if not multibot_key:
                log("Multibot API key not configured; hCaptcha cannot be solved automatically", "warn")
            else:
                _os.environ["MULTIBOT_API_KEY"] = multibot_key
        else:
            if not yescaptcha_key:
                log("YesCaptcha API key not configured; hCaptcha cannot be solved automatically", "warn")
            else:
                _os.environ["YESCAPTCHA_API_KEY"] = yescaptcha_key
        # Propagate both keys + the selected provider so the child flow can dispatch.
        if yescaptcha_key:
            _os.environ["YESCAPTCHA_API_KEY"] = yescaptcha_key
        if multibot_key:
            _os.environ["MULTIBOT_API_KEY"] = multibot_key
        _os.environ["CAPTCHA_PROVIDER"] = captcha_provider

        access_token = result.get("accessToken", "")
        profile_arn = FIXED_PROFILE_ARNS.get("BuilderId", "")
        log = self._reg_log
        cdk_code = self._reg_cdk_code.get().strip()

        if not cdk_code:
            log("⚠ CDK code not configured; skipping Pro trial subscription flow", "warn")
            return

        # Step 1: Query available plans
        log("Querying available subscription plans from AWS...", "info")
        subs = kiro_subscribe.list_available_subscriptions(access_token, profile_arn, log=log)
        if not subs.get("ok"):
            log("❌ Failed to fetch subscription plan list from AWS", "err")
            return

        # Found the KIRO_PRO plan
        plans = subs.get("plans", [])
        pro_type = None
        for p in plans:
            qt = p.get("qSubscriptionType", "")
            if "PRO" in qt.upper() and "PLUS" not in qt.upper():
                pro_type = qt
                break
        if not pro_type and plans:
            pro_type = plans[0].get("qSubscriptionType", "KIRO_PRO")
        if not pro_type:
            pro_type = "KIRO_PRO"

        log(f"✓ Detected subscription plan: {pro_type}", "info")

        # Step 2: Fetching the Stripe payment URL
        log("Generating Stripe checkout session URL...", "info")
        token_result = kiro_subscribe.create_subscription_token(
            access_token, profile_arn, pro_type, log=log
        )
        if not token_result.get("ok") or not token_result.get("url"):
            log("❌ Failed to generate Stripe payment URL", "err")
            return

        payment_url = token_result["url"]
        log(f"✓ Payment URL generated: {payment_url[:80]}...", "info")

        # Step 3: Pre-check — Open the Stripe page to determine whether $0 Trial
        log("Verifying trial status: checking Stripe checkout page for $0 trial eligibility...", "info")
        page_info = await kiro_subscribe.fetch_checkout_page_async(payment_url, log=log)
        if page_info:
            is_free = page_info.get("is_free_trial", False)
            total_due = page_info.get("total_due_today", "unknown")
            log(f"Amount due today: {total_due}", "info")
            if not is_free:
                log(f"⚠ Non-$0 trial detected (due today: {total_due}). Aborting to preserve CDK card.", "err")
                return
            log("✓ Confirmed $0 free trial. Proceeding with automated payment flow...", "ok")
        else:
            log("❌ Could not verify trial amount (payment link may be expired). Aborting.", "err")
            return

        # Step 4: Use EFunCard + Stripe Auto-pay
        log(f"Initiating Stripe checkout automation with CDK card (code: {cdk_code[:4]}****{cdk_code[-4:]})...", "info")
        captcha_cfg = {
            "yescaptcha_key": yescaptcha_key,
            "multibot_key": multibot_key,
            "provider": captcha_provider,
        }
        pay_result = await auto_pay(
            payment_url, cdk_code, captcha_config=captcha_cfg, headless=True, log=log
        )

        if pay_result and pay_result.get("ok"):
            log(f"✓ Pro trial subscription completed successfully for {result['email']}", "ok")
            try:
                rows = db_get_all(self.conn)
                for row in rows:
                    if row["email"] == result["email"]:
                        self.conn.execute(
                            "UPDATE accounts SET subscription=? WHERE id=?",
                            ("Pro", row["id"])
                        )
                        self.conn.commit()
                        break
                self.after(0, self._load_accounts_from_db)
            except Exception:
                pass
        elif pay_result and pay_result.get("status") == "not_free_trial":
            log(f"❌ Account not eligible for free trial: {pay_result.get('message', 'Unknown reason')}", "err")
        else:
            reason = pay_result.get("message", str(pay_result)) if pay_result else "Unknown error"
            log(f"❌ Pro trial subscription failed: {reason}", "err")

    # ─── Tab 4: Manual sign-in ─────────────────────────────────────────────────
    def _build_tab_manual_login(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  Manual sign-in  ")

        # Option row
        opts_frame = ttk.Frame(tab)
        opts_frame.pack(fill="x", pady=(0, 8))

        self._ml_headless = tk.BooleanVar(value=False)
        self._ml_auto_login = tk.BooleanVar(value=True)
        self._ml_clear_session = tk.BooleanVar(value=True)

        ttk.Checkbutton(opts_frame, text="Headless", variable=self._ml_headless).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(opts_frame, text="Auto sign-in", variable=self._ml_auto_login).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(opts_frame, text="Clear old login data", variable=self._ml_clear_session).pack(side="left", padx=(0, 12))

        # Sign-in method button
        method_frame = ttk.LabelFrame(tab, text="Select sign-in method", padding=10)
        method_frame.pack(fill="x", pady=(0, 8))

        btn_row = ttk.Frame(method_frame)
        btn_row.pack(fill="x")

        self._ml_method = tk.StringVar(value="builderid")

        ttk.Button(btn_row, text="Google", style="Green.TButton",
                   command=lambda: self._ml_launch("google")).pack(side="left", padx=(0, 8), pady=4)
        ttk.Button(btn_row, text="GitHub", style="Green.TButton",
                   command=lambda: self._ml_launch("github")).pack(side="left", padx=(0, 8), pady=4)
        ttk.Button(btn_row, text="AWS Builder ID", style="Green.TButton",
                   command=lambda: self._ml_launch("builderid")).pack(side="left", padx=(0, 8), pady=4)
        ttk.Button(btn_row, text="IAM Identity Center", style="Green.TButton",
                   command=lambda: self._ml_launch("iam")).pack(side="left", padx=(0, 8), pady=4)

        self._ml_stop_btn = ttk.Button(btn_row, text="Stop", style="Red.TButton",
                                       command=self._ml_stop, state="disabled")
        self._ml_stop_btn.pack(side="right", padx=(8, 0), pady=4)

        # Terminal output
        term_frame = ttk.Frame(tab)
        term_frame.pack(fill="both", expand=True)

        self._ml_term = tk.Text(term_frame, bg="#0d1117", fg="#c9d1d9",
                                font=("Consolas", 9), wrap="word", height=15)
        ml_scroll = ttk.Scrollbar(term_frame, orient="vertical", command=self._ml_term.yview)
        self._ml_term.configure(yscrollcommand=ml_scroll.set)
        self._ml_term.pack(side="left", fill="both", expand=True)
        ml_scroll.pack(side="right", fill="y")

        self._ml_term.tag_configure("info", foreground="#58a6ff")
        self._ml_term.tag_configure("ok", foreground="#2ecc71")
        self._ml_term.tag_configure("err", foreground="#f85149")
        self._ml_term.tag_configure("dbg", foreground="#8b949e")

        self._ml_queue = queue.Queue()
        self._ml_running = False
        self._ml_cancel = False

    # ─── Tab 4 Actions: Manual sign-in ─────────────────────────────────────────
    def _ml_log(self, msg, level="info"):
        self._ml_queue.put((msg, level))

    def _ml_poll_queue(self):
        has_msg = False
        try:
            while True:
                msg, level = self._ml_queue.get_nowait()
                has_msg = True
                ts = datetime.now().strftime("%H:%M:%S")
                prefix = {"info": "[*]", "ok": "[+]", "err": "[-]", "dbg": "[~]"}.get(level, "[?]")
                tag = level if level in ("info", "ok", "err", "dbg") else "info"
                self._ml_term.insert("end", f"{ts} {prefix} {msg}\n", tag)
                self._ml_term.see("end")
        except Exception:
            pass
        if self._ml_running or has_msg:
            self.after(100, self._ml_poll_queue)

    def _ml_launch(self, method):
        if self._ml_running:
            return
        self._ml_running = True
        self._ml_cancel = False
        self._ml_term.delete("1.0", "end")
        self._ml_term.insert("end", f"{datetime.now().strftime('%H:%M:%S')} [*] Starting {method} Sign in...\n", "info")
        self._ml_stop_btn.configure(state="normal")
        self.after(100, self._ml_poll_queue)

        headless = self._ml_headless.get()
        auto_login = self._ml_auto_login.get()
        clear_session = self._ml_clear_session.get()

        def _worker():
            try:
                import kiro_login
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    kiro_login.manual_login(
                        method=method,
                        headless=headless,
                        auto_login=auto_login,
                        clear_session=clear_session,
                        log=self._ml_log,
                        cancel_check=lambda: self._ml_cancel,
                    )
                )
                loop.close()
                if result:
                    self._ml_queue.put(("Login successful! Fetching account info...", "ok"))
                    # Reuse the local-import logic to fetch the real email and usage
                    access_token = result.get("accessToken", "")
                    refresh_token_val = result.get("refreshToken", "")
                    client_id = result.get("clientId", "")
                    client_secret = result.get("clientSecret", "")
                    provider = result.get("provider", "BuilderId")
                    region = result.get("region", "us-east-1")
                    client_id_hash = result.get("clientIdHash", "")

                    profile_arn = FIXED_PROFILE_ARNS.get(provider, "")
                    if not profile_arn and access_token:
                        profile_arn = list_profiles(access_token) or ""

                    email = ""
                    user_id = ""
                    usage_limit = 0
                    current_usage = 0
                    overage_cap = 0
                    current_overages = 0
                    overage_status = ""
                    overage_charges = 0.0
                    subscription = ""

                    if access_token and profile_arn:
                        usage_result = query_usage(access_token, profile_arn, require_email=True)
                        if usage_result["ok"]:
                            data = usage_result["data"]
                            user_info = data.get("userInfo", {})
                            if user_info:
                                email = user_info.get("email", "")
                            sub_info = data.get("subscriptionInfo", {})
                            if sub_info:
                                subscription = sub_info.get("subscriptionTitle", "") or sub_info.get("type", "")
                            breakdown_list = data.get("usageBreakdownList", [])
                            if breakdown_list:
                                b = breakdown_list[0]
                                usage_limit = int(b.get("usageLimit", b.get("usageLimitWithPrecision", 0)))
                                current_usage = int(b.get("currentUsage", b.get("currentUsageWithPrecision", 0)))
                                overage_cap = int(b.get("overageCap", b.get("overageCapWithPrecision", 0)))
                                current_overages = int(b.get("currentOverages", b.get("currentOveragesWithPrecision", 0)))
                                overage_charges = float(b.get("overageCharges", 0))
                            overage_cfg = data.get("overageConfiguration", {})
                            overage_status = overage_cfg.get("overageStatus", "")

                    if not email:
                        email, user_id = decode_jwt_email(access_token)

                    if not email and client_id and client_secret and refresh_token_val:
                        refresh_result = refresh_idc_token(client_id, client_secret, refresh_token_val, region)
                        if refresh_result:
                            access_token = refresh_result["accessToken"]
                            refresh_token_val = refresh_result["refreshToken"]
                            id_token = refresh_result.get("idToken", "")
                            if id_token:
                                email, user_id = decode_jwt_email(id_token)

                    if not email and access_token:
                        email, user_id = get_userinfo_email(access_token, region)

                    if not email:
                        email = result.get("email", f"{provider}_unknown")

                    self._ml_queue.put((f"Email: {email}", "ok"))
                    if subscription:
                        self._ml_queue.put((f"Subscription: {subscription}", "ok"))
                    if usage_limit:
                        self._ml_queue.put((f"Usage: {current_usage}/{usage_limit}", "ok"))

                    expires_at = result.get("expiresAt", "")
                    if expires_at and "/" in expires_at:
                        try:
                            dt = datetime.strptime(expires_at, "%Y/%m/%d %H:%M:%S")
                            expires_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            pass

                    account_data = {
                        "email": email,
                        "provider": provider,
                        "authMethod": result.get("authMethod", "IdC"),
                        "accessToken": access_token,
                        "refreshToken": refresh_token_val,
                        "expiresAt": expires_at,
                        "clientId": client_id,
                        "clientSecret": client_secret,
                        "clientIdHash": client_id_hash,
                        "region": region,
                        "profileArn": profile_arn,
                        "userId": user_id,
                        "usageLimit": usage_limit,
                        "currentUsage": current_usage,
                        "overageCap": overage_cap,
                        "currentOverages": current_overages,
                        "overageStatus": overage_status,
                        "overageCharges": overage_charges,
                        "subscription": subscription,
                        "lastQueryTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S") if profile_arn else None,
                    }
                    try:
                        db_upsert_account(self.conn, account_data)
                        self._ml_queue.put(("Account imported into the database", "ok"))
                    except Exception as e:
                        self._ml_queue.put((f"Database import failed: {e}", "err"))
                    self.after(0, self._load_accounts_from_db)
                else:
                    self._ml_queue.put(("Login flow ended (no result returned))", "err"))
            except Exception as e:
                self._ml_queue.put((f"Login error: {e}", "err"))
                try:
                    import traceback
                    self._ml_queue.put((traceback.format_exc(), "dbg"))
                except Exception:
                    pass
            finally:
                self._ml_running = False
                self.after(0, lambda: self._ml_stop_btn.configure(state="disabled"))

        threading.Thread(target=_worker, daemon=True).start()

    def _ml_stop(self):
        self._ml_cancel = True
        self._ml_log("User requested stop...", "err")

    # ─── Logging ─────────────────────────────────────────────────────────
    def _log(self, msg, tag="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {msg}\n", tag)
        self.log_text.see("end")

    # ─── Data Loading ────────────────────────────────────────────────────
    def _load_accounts_from_db(self):
        for item in self.acc_tree.get_children():
            self.acc_tree.delete(item)

        rows = db_get_all(self.conn)
        for row in rows:
            usage_str = f"{row['currentUsage']}/{row['usageLimit']}" if row['usageLimit'] else "-"
            expires = row["expiresAt"] or "none "
            expired = is_token_expired(expires)
            status = "Expired" if expired else "valid"
            overage = row["overageStatus"] or "Disabled"
            sub = format_subscription(row["subscription"])

            self.acc_tree.insert("", "end", iid=str(row["id"]), values=(
                row["id"], row["email"] or "", row["provider"] or "",
                row["authMethod"] or "", sub, overage, usage_str, expires, status
            ))

        count = len(rows)
        providers = {}
        for r in rows:
            p = r["provider"] or "unknown"
            providers[p] = providers.get(p, 0) + 1
        prov_str = "  ".join(f"{k}:{v}" for k, v in providers.items())
        self.lbl_acc_stats.configure(text=f"total  {count}  account(s)  {prov_str}")

    # ─── Tab 1 Actions ───────────────────────────────────────────────────
    def _refresh_after_import(self, email="", queue=None):
        """After import, refresh the token immediately and sync the subscription info"""
        log = queue.put if queue else lambda m, *_: self._log(m)
        try:
            rows = db_get_all(self.conn)
            for row in rows:
                if email and row["email"] != email:
                    continue
                if not row["refreshToken"]:
                    continue
                at, rt, ea, err = do_refresh_token(row)
                if err:
                    log((f"Token refresh failed ({row['email']}): {err}", "err"))
                    continue
                db_update_token(self.conn, row["id"], at, rt, ea)
                _sync_subscription_after_refresh(self.conn, row, at)
                log((f"Token Refreshed: {row['email']}", "ok"))
                if not email:
                    break
        except Exception as e:
            if queue:
                log((f"Refresh error: {e}", "err"))

    def _import_local(self):
        self._log("Importing from the local Kiro install...")
        def _do():
            ok, msg = import_from_local_kiro(self.conn)
            if ok:
                self.after(0, lambda: self._log(msg, "success"))
            else:
                self.after(0, lambda: self._log(msg, "error"))
            self.after(0, self._load_accounts_from_db)
        threading.Thread(target=_do, daemon=True).start()

    def _import_json(self):
        path = filedialog.askopenfilename(
            title="Select a JSON account file",
            filetypes=[("JSON File", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        self._log(f"Importing: {Path(path).name}")
        def _do():
            try:
                count, emails = import_from_json_file(self.conn, path)
                self.after(0, lambda: self._log(f"Successfully imported {count}  account(s)", "success"))
                self.after(0, self._load_accounts_from_db)
                if emails:
                    self._refresh_imported_parallel(emails)
            except Exception as e:
                self.after(0, lambda: self._log(f"Import failed: {e}", "error"))
            self.after(0, self._load_accounts_from_db)
        threading.Thread(target=_do, daemon=True).start()

    def _refresh_imported_parallel(self, emails):
        """Refreshing accounts for the given email list in parallel token"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        rows = db_get_all(self.conn)
        targets = [r for r in rows if r["email"] in emails and r["refreshToken"]]
        if not targets:
            return

        def _refresh_one(row):
            try:
                at, rt, ea, err = do_refresh_token(row)
                if err:
                    return row["email"], False, err
                db_update_token(self.conn, row["id"], at, rt, ea)
                _sync_subscription_after_refresh(self.conn, row, at)
                return row["email"], True, None
            except Exception as e:
                return row["email"], False, str(e)

        refreshed = 0
        with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
            futures = {pool.submit(_refresh_one, r): r for r in targets}
            for fut in as_completed(futures):
                email, ok, err = fut.result()
                if ok:
                    refreshed += 1
                else:
                    self.after(0, lambda e=email, er=err: self._log(f"Refresh failed ({e}): {er}", "warn"))
        if refreshed:
            self.after(0, lambda: self._log(f" refreshed in parallel {refreshed}/{len(targets)}  imported accounts", "success"))
            self.after(0, self._load_accounts_from_db)

    def _refresh_all_tokens_silent(self):
        """Silently refresh every account token"""
        rows = db_get_all(self.conn)
        refreshed = 0
        for row in rows:
            if not row["refreshToken"]:
                continue
            try:
                at, rt, ea, err = do_refresh_token(row)
                if not err:
                    db_update_token(self.conn, row["id"], at, rt, ea)
                    _sync_subscription_after_refresh(self.conn, row, at)
                    refreshed += 1
            except Exception:
                pass
        if refreshed:
            self.after(0, lambda: self._log(f"Refreshed {refreshed}  account(s) Token", "success"))
        self.after(0, self._load_accounts_from_db)

    def _start_auto_refresh(self):
        """Starting the auto-refresh timer"""
        if self._auto_refresh_id:
            self.after_cancel(self._auto_refresh_id)
            self._auto_refresh_id = None
        try:
            minutes = int(self._auto_refresh_min.get().strip() or "0")
        except ValueError:
            minutes = 0
        if minutes > 0:
            ms = minutes * 60 * 1000
            self._auto_refresh_id = self.after(ms, self._auto_refresh_tick)

    def _auto_refresh_tick(self):
        """Scheduled-refresh callback"""
        self._auto_refresh_id = None
        def _do():
            self._refresh_all_tokens_silent()
        threading.Thread(target=_do, daemon=True).start()
        self._start_auto_refresh()

    def _export_json(self):
        path = filedialog.asksaveasfilename(
            title="Export JSON",
            defaultextension=".json",
            filetypes=[("JSON File", "*.json")],
            initialfile="kiro_accounts_export.json",
        )
        if not path:
            return
        try:
            count = export_to_json(self.conn, path)
            self._log(f"Successfully exported {count}  accounts to  {Path(path).name}", "success")
        except Exception as e:
            self._log(f"Export failed: {e}", "error")

    def _delete_selected(self):
        sel = self.acc_tree.selection()
        if not sel:
            messagebox.showwarning("Info", "Select the account(s) you want to delete first")
            return
        if not messagebox.askyesno("Confirm", f"Confirm deletion of the selected {len(sel)}  account(s)?"):
            return
        for iid in sel:
            db_delete(self.conn, int(iid))
        self._load_accounts_from_db()
        self._log(f"Deleted {len(sel)}  account(s)", "warn")

    def _refresh_selected_token(self):
        sel = self.acc_tree.selection()
        if not sel:
            messagebox.showwarning("Info", "Select the account(s) you want to refresh first")
            return
        if self.running:
            return
        self.running = True
        self._log(f"Refreshing {len(sel)}  accounts' Token...")

        def _do():
            success = 0
            for iid in sel:
                row_id = int(iid)
                row = self.conn.execute("SELECT * FROM accounts WHERE id=?", (row_id,)).fetchone()
                if not row:
                    continue
                at, rt, ea, err = do_refresh_token(row)
                if at:
                    db_update_token(self.conn, row_id, at, rt, ea)
                    _sync_subscription_after_refresh(self.conn, row, at)
                    # Re-read row to get updated subscription
                    updated = self.conn.execute("SELECT * FROM accounts WHERE id=?", (row_id,)).fetchone()
                    sub_display = format_subscription(updated["subscription"]) if updated else "-"
                    self.after(0, lambda i=iid, e=ea: self.acc_tree.set(i, "expires", e))
                    self.after(0, lambda i=iid: self.acc_tree.set(i, "status", "valid"))
                    self.after(0, lambda i=iid, s=sub_display: self.acc_tree.set(i, "subscription", s))
                    success += 1
                else:
                    self.after(0, lambda i=iid, e=err: self.acc_tree.set(i, "status", e))
                    self.after(0, lambda e=err, em=row["email"]:
                               self._log(f"{em}: {e}", "error"))
            self.after(0, lambda: self._log(f"Token Refresh complete: {success}/{len(sel)} succeeded", "success"))
            self.running = False
        threading.Thread(target=_do, daemon=True).start()

    def _health_check(self):
        if self.running:
            return
        rows = db_get_all(self.conn)
        if not rows:
            messagebox.showinfo("Info", "Database is empty")
            return
        self.running = True
        self.acc_progress["value"] = 0
        self.acc_progress["maximum"] = len(rows)
        self._log("Starting health check...")

        def _do():
            valid = 0
            expired_count = 0
            refreshed = 0
            failed = 0
            for i, row in enumerate(rows, 1):
                iid = str(row["id"])
                if not is_token_expired(row["expiresAt"]):
                    self.after(0, lambda i2=iid: self.acc_tree.set(i2, "status", "valid"))
                    valid += 1
                else:
                    at, rt, ea, err = do_refresh_token(row)
                    if at:
                        db_update_token(self.conn, row["id"], at, rt, ea)
                        _sync_subscription_after_refresh(self.conn, row, at)
                        updated = self.conn.execute("SELECT * FROM accounts WHERE id=?", (row["id"],)).fetchone()
                        sub_display = format_subscription(updated["subscription"]) if updated else "-"
                        self.after(0, lambda i2=iid, e=ea, s=sub_display: (
                            self.acc_tree.set(i2, "expires", e),
                            self.acc_tree.set(i2, "status", "Refreshed"),
                            self.acc_tree.set(i2, "subscription", s),
                        ))
                        refreshed += 1
                    else:
                        self.after(0, lambda i2=iid: self.acc_tree.set(i2, "status", "invalid"))
                        failed += 1
                        expired_count += 1
                self.after(0, lambda v=i: self.acc_progress.configure(value=v))

            msg = f"Health check complete: valid {valid}, Refreshed {refreshed}, invalid {failed}"
            self.after(0, lambda: self._log(msg, "success" if failed == 0 else "warn"))
            self.running = False
        threading.Thread(target=_do, daemon=True).start()

    # ─── Models Panel ────────────────────────────────────────────────────
    def _toggle_models_panel(self):
        if self._models_visible.get():
            self.models_frame.pack_forget()
            self._models_visible.set(False)
            self.btn_models_toggle.configure(text="▶ Available models (click to expand))")
        else:
            self.models_frame.pack(fill="both", expand=True, pady=(2, 0), before=self._log_label)
            self._models_visible.set(True)
            self.btn_models_toggle.configure(text="▼ Available models (click to collapse))")
            self._show_cached_models()

    def _on_acc_select(self, event):
        sel = self.acc_tree.selection()
        count = len(sel)
        if count > 0:
            self.lbl_sel_info.configure(text=f"Selected {count}  account(s)")
        else:
            self.lbl_sel_info.configure(text="")
        if self._models_visible.get():
            self._show_cached_models()

    def _on_acc_right_click(self, event):
        iid = self.acc_tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.acc_tree.selection():
            self.acc_tree.selection_set(iid)
        sel = self.acc_tree.selection()
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Copy account JSON", command=lambda: self._copy_account_json(iid))
        menu.add_command(label="Copy email", command=lambda: self._copy_field(iid, "email"))
        menu.add_command(label="Copy password", command=lambda: self._copy_field(iid, "password"))
        menu.add_command(label="Copy Access Token", command=lambda: self._copy_field(iid, "accessToken"))
        menu.add_separator()
        if len(sel) > 1:
            menu.add_command(label=f"Copy selected {len(sel)}  account(s) JSON", command=self._copy_selected_json)
            menu.add_command(label=f"Copy selected {len(sel)}  email(s)", command=self._copy_selected_emails)
            menu.add_separator()
        menu.add_command(label="View account details", command=lambda: self._show_account_detail(iid))
        menu.add_command(label="Query this account's quota", command=lambda: self._query_single_usage(iid))
        menu.add_command(label="Refresh this account Token", command=lambda: self._refresh_single_token(iid))
        menu.add_command(label="Inject this account locally", command=lambda: self._inject_single(iid))
        menu.add_separator()
        if len(sel) > 1:
            menu.add_command(label=f"Query selected {len(sel)}  quotas", command=self._query_selected_usage)
            menu.add_command(label=f"Bulk enable overage ({len(sel)} )", command=self._batch_enable_overage)
            menu.add_separator()
        menu.add_command(label="Select all", command=self._select_all_accounts)
        menu.add_command(label="Invert selection", command=self._invert_selection)
        menu.add_separator()
        menu.add_command(label="Delete selected", command=self._delete_selected)
        menu.tk_popup(event.x_root, event.y_root)

    def _on_acc_double_click(self, event):
        iid = self.acc_tree.identify_row(event.y)
        if iid:
            self._show_account_detail(iid)

    def _show_account_detail(self, iid):
        row = self.conn.execute("SELECT * FROM accounts WHERE id=?", (int(iid),)).fetchone()
        if not row:
            return
        info_lines = [
            f"ID: {row['id']}",
            f"Email: {row['email'] or '(unknown)'}",
            f"Password: {row['password'] or '(Unsaved)'}",
            f"Sign-in method: {row['provider'] or '-'}",
            f"Auth type: {row['authMethod'] or '-'}",
            f"Subscription: {format_subscription(row['subscription'])}",
            f"Overage status: {row['overageStatus'] or 'Disabled'}",
            f"Usage: {row['currentUsage']}/{row['usageLimit']}",
            f"Tokenexpired: {row['expiresAt'] or 'none '}",
            f"Region: {row['region'] or '-'}",
            f"Created at: {row['createdAt'] or '-'}",
            f"Updated at: {row['updatedAt'] or '-'}",
        ]
        messagebox.showinfo("Account details", "\n".join(info_lines))

    def _copy_field(self, iid, field):
        row = self.conn.execute("SELECT * FROM accounts WHERE id=?", (int(iid),)).fetchone()
        if row and row[field]:
            self.clipboard_clear()
            self.clipboard_append(row[field])
            self._log(f"Copied {field}  to clipboard", "success")

    def _account_to_json_dict(self, row):
        return {
            "email": row["email"],
            "password": row["password"] or "",
            "provider": row["provider"],
            "authMethod": row["authMethod"],
            "accessToken": row["accessToken"],
            "refreshToken": row["refreshToken"],
            "expiresAt": row["expiresAt"],
            "clientId": row["clientId"],
            "clientSecret": row["clientSecret"],
            "clientIdHash": row["clientIdHash"],
            "region": row["region"],
            "profileArn": row["profileArn"],
            "userId": row["userId"],
            "subscription": row["subscription"] or "",
            "usageData": {
                "usageBreakdownList": [{
                    "usageLimit": row["usageLimit"],
                    "currentUsage": row["currentUsage"],
                    "overageCap": row["overageCap"],
                    "currentOverages": row["currentOverages"],
                    "overageCharges": row["overageCharges"],
                }],
                "overageConfiguration": {
                    "overageStatus": row["overageStatus"] or "",
                },
            },
        }

    def _copy_account_json(self, iid):
        row = self.conn.execute("SELECT * FROM accounts WHERE id=?", (int(iid),)).fetchone()
        if not row:
            return
        data = self._account_to_json_dict(row)
        text = json.dumps(data, indent=2, ensure_ascii=False)
        self.clipboard_clear()
        self.clipboard_append(text)
        self._log(f"Copied {row['email']}  JSON copied to clipboard", "success")

    def _copy_selected_json(self):
        sel = self.acc_tree.selection()
        if not sel:
            messagebox.showwarning("Info", "Select an account first")
            return
        accounts = []
        for iid in sel:
            row = self.conn.execute("SELECT * FROM accounts WHERE id=?", (int(iid),)).fetchone()
            if row:
                accounts.append(self._account_to_json_dict(row))
        if not accounts:
            return
        text = json.dumps(accounts, indent=2, ensure_ascii=False)
        self.clipboard_clear()
        self.clipboard_append(text)
        self._log(f"Copied {len(accounts)}  account(s) JSON copied to clipboard", "success")

    def _copy_selected_emails(self):
        sel = self.acc_tree.selection()
        if not sel:
            messagebox.showwarning("Info", "Select an account first")
            return
        emails = []
        for iid in sel:
            row = self.conn.execute("SELECT email FROM accounts WHERE id=?", (int(iid),)).fetchone()
            if row and row["email"]:
                emails.append(row["email"])
        if emails:
            self.clipboard_clear()
            self.clipboard_append("\n".join(emails))
            self._log(f"Copied {len(emails)}  emails copied to clipboard", "success")

    def _select_all_accounts(self):
        items = self.acc_tree.get_children()
        if items:
            self.acc_tree.selection_set(items)
            self.lbl_sel_info.configure(text=f"Selected {len(items)}  account(s)")

    def _invert_selection(self):
        all_items = set(self.acc_tree.get_children())
        selected = set(self.acc_tree.selection())
        new_sel = all_items - selected
        if new_sel:
            self.acc_tree.selection_set(list(new_sel))
        else:
            self.acc_tree.selection_remove(*all_items)
        count = len(new_sel)
        self.lbl_sel_info.configure(text=f"Selected {count}  account(s)" if count else "")

    def _refresh_single_token(self, iid):
        row_id = int(iid)
        row = self.conn.execute("SELECT * FROM accounts WHERE id=?", (row_id,)).fetchone()
        if not row:
            return
        self._log(f"Refreshing {row['email']} 's  Token...")

        def _do():
            at, rt, ea, err = do_refresh_token(row)
            if at:
                db_update_token(self.conn, row_id, at, rt, ea)
                _sync_subscription_after_refresh(self.conn, row, at)
                updated = self.conn.execute("SELECT * FROM accounts WHERE id=?", (row_id,)).fetchone()
                sub_display = format_subscription(updated["subscription"]) if updated else "-"
                self.after(0, lambda: self.acc_tree.set(iid, "expires", ea))
                self.after(0, lambda: self.acc_tree.set(iid, "status", "valid"))
                self.after(0, lambda: self.acc_tree.set(iid, "subscription", sub_display))
                self.after(0, lambda: self._log(f"{row['email']} Token Refresh succeeded", "success"))
            else:
                self.after(0, lambda: self.acc_tree.set(iid, "status", err or "failed"))
                self.after(0, lambda: self._log(f"{row['email']}: {err}", "error"))
        threading.Thread(target=_do, daemon=True).start()

    def _refresh_all_tokens(self):
        rows = db_get_all(self.conn)
        targets = [r for r in rows if r["refreshToken"]]
        if not targets:
            messagebox.showinfo("Info", "No accounts available to refresh")
            return
        if self.running:
            return
        self.running = True
        self.acc_progress["value"] = 0
        self.acc_progress["maximum"] = len(targets)
        self._log(f"Refreshing all {len(targets)}  accounts' Token...")

        def _do():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            success = 0

            def _refresh_one(r):
                at, rt, ea, err = do_refresh_token(r)
                if at:
                    db_update_token(self.conn, r["id"], at, rt, ea)
                    _sync_subscription_after_refresh(self.conn, r, at)
                    return r, True, ea, None
                return r, False, None, err

            with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
                futures = {pool.submit(_refresh_one, r): r for r in targets}
                for i, fut in enumerate(as_completed(futures), 1):
                    r, ok, ea, err = fut.result()
                    iid = str(r["id"])
                    if ok:
                        updated = self.conn.execute("SELECT * FROM accounts WHERE id=?", (r["id"],)).fetchone()
                        sub_display = format_subscription(updated["subscription"]) if updated else "-"
                        self.after(0, lambda i2=iid, e=ea, s=sub_display: (
                            self.acc_tree.set(i2, "expires", e),
                            self.acc_tree.set(i2, "status", "valid"),
                            self.acc_tree.set(i2, "subscription", s),
                        ))
                        success += 1
                    else:
                        self.after(0, lambda i2=iid: self.acc_tree.set(i2, "status", "failed"))
                    self.after(0, lambda v=i: self.acc_progress.configure(value=v))

            self.after(0, lambda: self._log(
                f"Refresh all complete: {success}/{len(targets)} succeeded",
                "success" if success == len(targets) else "warn"))
            self.running = False
        threading.Thread(target=_do, daemon=True).start()

    def _show_cached_models(self):
        sel = self.acc_tree.selection()
        self.models_text.delete("1.0", "end")
        if not sel:
            self.models_text.insert("end", "  Select an account, then click 'Query models'\n", "dim")
            return
        row_id = int(sel[0])
        if row_id in self._models_cache:
            self._render_models(self._models_cache[row_id])
        else:
            self.models_text.insert("end", "  Not queried yet — click the 'Query models' button\n", "dim")

    def _query_selected_models(self):
        sel = self.acc_tree.selection()
        if not sel:
            messagebox.showwarning("Info", "Select an account first")
            return
        row_id = int(sel[0])
        row = self.conn.execute("SELECT * FROM accounts WHERE id=?", (row_id,)).fetchone()
        if not row:
            return
        self._log(f"Querying {row['email']}  available models for...")
        if not self._models_visible.get():
            self._toggle_models_panel()

        def _do():
            access_token, err = get_valid_token(row, self.conn)
            if not access_token:
                self.after(0, lambda: self._log(f"Token invalid: {err}", "error"))
                return
            provider = row["provider"] or ""
            profile_arn = row["profileArn"] or FIXED_PROFILE_ARNS.get(provider, "")
            if not profile_arn:
                profile_arn = list_profiles(access_token) or ""
            if not profile_arn:
                self.after(0, lambda: self._log("Could not fetch profileArn", "error"))
                return
            result = list_available_models(access_token, profile_arn)
            if result["ok"]:
                models_data = {
                    "models": result["models"],
                    "defaultModel": result["defaultModel"],
                }
                self._models_cache[row_id] = models_data
                self.after(0, lambda: self._render_models(models_data))
                self.after(0, lambda: self._log(
                    f"Found {len(result['models'])}  available models", "success"))
            else:
                err_info = result.get("error", {})
                msg = err_info.get("message") or err_info.get("Message") or str(err_info)[:80]
                self.after(0, lambda: self._log(f"Model query failed: {msg}", "error"))
                self.after(0, lambda: self.models_text.delete("1.0", "end"))
                self.after(0, lambda m=msg: self.models_text.insert("end", f"  Query failed: {m}\n", "dim"))
        threading.Thread(target=_do, daemon=True).start()

    def _render_models(self, models_data):
        self.models_text.delete("1.0", "end")
        default_model = models_data.get("defaultModel")
        models = models_data.get("models", [])

        if default_model:
            self.models_text.insert("end", "  Default model: ", "title")
            name = default_model.get("modelName", default_model.get("modelId", ""))
            self.models_text.insert("end", f"{name}\n", "default")

        self.models_text.insert("end", f"\n  total  {len(models)}  available models:\n", "title")
        self.models_text.insert("end", "  " + "─" * 70 + "\n", "dim")

        for m in models:
            name = m.get("modelName", "")
            mid = m.get("modelId", "")
            desc = m.get("description", "")
            rate = m.get("rateMultiplier")
            rate_unit = m.get("rateUnit", "")
            inputs = m.get("supportedInputTypes", [])

            is_default = default_model and mid == default_model.get("modelId")
            tag = "default" if is_default else "model"
            marker = " ★" if is_default else ""

            self.models_text.insert("end", f"  {name}{marker}\n", tag)
            self.models_text.insert("end", f"    ID: {mid}", "dim")
            if rate is not None:
                self.models_text.insert("end", f"  |  rate: {rate}x/{rate_unit}", "dim")
            if inputs:
                self.models_text.insert("end", f"  |  Input: {', '.join(inputs)}", "dim")
            self.models_text.insert("end", "\n", "dim")
            if desc:
                self.models_text.insert("end", f"    {desc}\n", "dim")

    # ─── Usage & Batch Actions (merged into accounts tab) ─────────────────
    def _query_all_usage(self):
        if self.running:
            return
        rows = db_get_all(self.conn)
        if not rows:
            messagebox.showinfo("Info", "Database is empty")
            return
        self.running = True
        self.acc_progress["value"] = 0
        self.acc_progress["maximum"] = len(rows)
        self._log("Querying quota for all accounts...")

        def _do():
            total_used = 0
            total_limit = 0
            total_overage = 0.0

            for i, row in enumerate(rows, 1):
                row_id = row["id"]
                email = row["email"] or ""
                provider = row["provider"] or ""
                iid = str(row_id)

                access_token, err = get_valid_token(row, self.conn)
                if not access_token:
                    self.after(0, lambda i2=iid: self.acc_tree.set(i2, "usage", "TokenError"))
                    self.after(0, lambda v=i: self.acc_progress.configure(value=v))
                    continue

                profile_arn = row["profileArn"] or FIXED_PROFILE_ARNS.get(provider, "")
                if not profile_arn:
                    profile_arn = list_profiles(access_token) or ""

                if not profile_arn:
                    self.after(0, lambda i2=iid: self.acc_tree.set(i2, "usage", "none ARN"))
                    self.after(0, lambda v=i: self.acc_progress.configure(value=v))
                    continue

                result = query_usage(access_token, profile_arn)
                if result["ok"]:
                    data = result["data"]
                    bl = data.get("usageBreakdownList", [])
                    b = bl[0] if bl else {}
                    used = int(b.get("currentUsage", b.get("currentUsageWithPrecision", 0)))
                    limit = int(b.get("usageLimit", b.get("usageLimitWithPrecision", 0)))
                    ov_used = int(b.get("currentOverages", b.get("currentOveragesWithPrecision", 0)))
                    ov_cap = int(b.get("overageCap", b.get("overageCapWithPrecision", 0)))
                    ov_cost = float(b.get("overageCharges", 0))
                    ov_status = data.get("overageConfiguration", {}).get("overageStatus", "")
                    sub_info = data.get("subscriptionInfo", {})
                    sub_raw = sub_info.get("subscriptionTitle", "") or sub_info.get("type", "") if sub_info else ""

                    total_used += used
                    total_limit += limit
                    total_overage += ov_cost

                    db_update_usage(self.conn, row_id, {
                        "usageLimit": limit, "currentUsage": used,
                        "overageCap": ov_cap, "currentOverages": ov_used,
                        "overageStatus": ov_status, "overageCharges": ov_cost,
                        "subscription": sub_raw,
                    })

                    usage_str = f"{used}/{limit}"
                    self.after(0, lambda i2=iid, u=usage_str, o=ov_status or "Disabled", s=sub_raw: (
                        self.acc_tree.set(i2, "usage", u),
                        self.acc_tree.set(i2, "overage", o),
                        self.acc_tree.set(i2, "subscription", format_subscription(s)),
                    ))
                else:
                    self.after(0, lambda i2=iid: self.acc_tree.set(i2, "usage", "Query failed"))

                self.after(0, lambda v=i: self.acc_progress.configure(value=v))

            msg = f"Quota query complete: used {total_used} | Total quota {total_limit} | Overage cost ${total_overage:.2f}"
            self.after(0, lambda: self._log(msg, "success"))
            self.after(0, lambda: self.lbl_acc_stats.configure(
                text=f"used {total_used}/{total_limit}  Overage ${total_overage:.2f}"))
            self.running = False
        threading.Thread(target=_do, daemon=True).start()

    def _query_selected_usage(self):
        sel = self.acc_tree.selection()
        if not sel:
            messagebox.showwarning("Info", "Select an account first")
            return
        if self.running:
            return
        self.running = True
        self.acc_progress["value"] = 0
        self.acc_progress["maximum"] = len(sel)
        self._log(f"Querying {len(sel)}  accounts' quota...")

        def _do():
            for i, iid in enumerate(sel, 1):
                row_id = int(iid)
                row = self.conn.execute("SELECT * FROM accounts WHERE id=?", (row_id,)).fetchone()
                if not row:
                    continue
                access_token, err = get_valid_token(row, self.conn)
                if not access_token:
                    self.after(0, lambda i2=iid: self.acc_tree.set(i2, "usage", "TokenError"))
                    self.after(0, lambda v=i: self.acc_progress.configure(value=v))
                    continue

                provider = row["provider"] or ""
                profile_arn = row["profileArn"] or FIXED_PROFILE_ARNS.get(provider, "")
                if not profile_arn:
                    profile_arn = list_profiles(access_token) or ""
                if not profile_arn:
                    self.after(0, lambda i2=iid: self.acc_tree.set(i2, "usage", "none ARN"))
                    self.after(0, lambda v=i: self.acc_progress.configure(value=v))
                    continue

                result = query_usage(access_token, profile_arn)
                if result["ok"]:
                    data = result["data"]
                    bl = data.get("usageBreakdownList", [])
                    b = bl[0] if bl else {}
                    used = int(b.get("currentUsage", b.get("currentUsageWithPrecision", 0)))
                    limit = int(b.get("usageLimit", b.get("usageLimitWithPrecision", 0)))
                    ov_used = int(b.get("currentOverages", b.get("currentOveragesWithPrecision", 0)))
                    ov_cap = int(b.get("overageCap", b.get("overageCapWithPrecision", 0)))
                    ov_cost = float(b.get("overageCharges", 0))
                    ov_status = data.get("overageConfiguration", {}).get("overageStatus", "")
                    sub_info = data.get("subscriptionInfo", {})
                    sub_raw = sub_info.get("subscriptionTitle", "") or sub_info.get("type", "") if sub_info else ""

                    db_update_usage(self.conn, row_id, {
                        "usageLimit": limit, "currentUsage": used,
                        "overageCap": ov_cap, "currentOverages": ov_used,
                        "overageStatus": ov_status, "overageCharges": ov_cost,
                        "subscription": sub_raw,
                    })

                    usage_str = f"{used}/{limit}"
                    self.after(0, lambda i2=iid, u=usage_str, o=ov_status or "Disabled", s=sub_raw: (
                        self.acc_tree.set(i2, "usage", u),
                        self.acc_tree.set(i2, "overage", o),
                        self.acc_tree.set(i2, "subscription", format_subscription(s)),
                    ))
                else:
                    self.after(0, lambda i2=iid: self.acc_tree.set(i2, "usage", "Query failed"))

                self.after(0, lambda v=i: self.acc_progress.configure(value=v))

            self.after(0, lambda: self._log(f"Quota query complete ({len(sel)}  account(s))", "success"))
            self.running = False
        threading.Thread(target=_do, daemon=True).start()

    def _query_single_usage(self, iid):
        row_id = int(iid)
        row = self.conn.execute("SELECT * FROM accounts WHERE id=?", (row_id,)).fetchone()
        if not row:
            return
        self._log(f"Querying {row['email']} 's quota...")

        def _do():
            access_token, err = get_valid_token(row, self.conn)
            if not access_token:
                self.after(0, lambda: self._log(f"Token Fetch failed: {err}", "error"))
                return

            provider = row["provider"] or ""
            profile_arn = row["profileArn"] or FIXED_PROFILE_ARNS.get(provider, "")
            if not profile_arn:
                profile_arn = list_profiles(access_token) or ""
            if not profile_arn:
                self.after(0, lambda: self._log("Could not fetch profileArn", "error"))
                return

            result = query_usage(access_token, profile_arn)
            if result["ok"]:
                data = result["data"]
                bl = data.get("usageBreakdownList", [])
                b = bl[0] if bl else {}
                used = int(b.get("currentUsage", b.get("currentUsageWithPrecision", 0)))
                limit = int(b.get("usageLimit", b.get("usageLimitWithPrecision", 0)))
                ov_used = int(b.get("currentOverages", b.get("currentOveragesWithPrecision", 0)))
                ov_cap = int(b.get("overageCap", b.get("overageCapWithPrecision", 0)))
                ov_cost = float(b.get("overageCharges", 0))
                ov_status = data.get("overageConfiguration", {}).get("overageStatus", "")
                sub_info = data.get("subscriptionInfo", {})
                sub_raw = sub_info.get("subscriptionTitle", "") or sub_info.get("type", "") if sub_info else ""

                db_update_usage(self.conn, row_id, {
                    "usageLimit": limit, "currentUsage": used,
                    "overageCap": ov_cap, "currentOverages": ov_used,
                    "overageStatus": ov_status, "overageCharges": ov_cost,
                    "subscription": sub_raw,
                })

                usage_str = f"{used}/{limit}"
                pct = (used / limit * 100) if limit > 0 else 0
                lines = [
                    f"Account: {row['email']}",
                    f"Subscription: {format_subscription(sub_raw)}",
                    f"Base quota: {used}/{limit} ({pct:.1f}%)",
                    f"Overage used: {ov_used}/{ov_cap}",
                    f"Overage cost: ${ov_cost:.2f}",
                    f"Overage status: {ov_status or 'Disabled'}",
                ]
                self.after(0, lambda i2=iid, u=usage_str, o=ov_status or "Disabled", s=sub_raw: (
                    self.acc_tree.set(i2, "usage", u),
                    self.acc_tree.set(i2, "overage", o),
                    self.acc_tree.set(i2, "subscription", format_subscription(s)),
                ))
                self.after(0, lambda m="\n".join(lines): messagebox.showinfo("Quota details", m))
            else:
                err_data = result.get("error", {})
                msg = translate_api_error(err_data)
                self.after(0, lambda: self._log(f"Query failed: {msg}", "error"))
        threading.Thread(target=_do, daemon=True).start()

    # ─── Batch Actions (merged into accounts tab) ─────────────────────────
    def _batch_enable_overage(self):
        if self.running:
            return
        sel = self.acc_tree.selection()
        rows_all = db_get_all(self.conn)
        if sel:
            sel_ids = {int(s) for s in sel}
            rows = [r for r in rows_all if r["id"] in sel_ids]
        else:
            rows = rows_all
        if not rows:
            messagebox.showinfo("Info", "No accounts available to operate on")
            return
        self.running = True
        self.acc_progress["value"] = 0
        self.acc_progress["maximum"] = len(rows)
        self._log(f"Starting bulk overage enable... (total {len(rows)}  account(s))")

        def _do():
            success = 0
            skipped = 0
            failed = 0
            try:
                for i, row in enumerate(rows, 1):
                    if not self.running:
                        self.after(0, lambda: self._log("Bulk operation stopped", "warn"))
                        break
                    row_id = row["id"]
                    email = row["email"] or ""
                    provider = row["provider"] or ""
                    iid = str(row_id)
                    self.after(0, lambda idx=i, total=len(rows), e=email:
                        self._log(f"[{idx}/{total}] Process: {e}"))

                    if row["overageStatus"] == "ENABLED":
                        self.after(0, lambda i2=iid: self.acc_tree.set(i2, "overage", "Enabled"))
                        skipped += 1
                        self.after(0, lambda v=i: self.acc_progress.configure(value=v))
                        continue

                    sub = (row["subscription"] or "")
                    sub_lower = sub.lower()
                    if sub_lower and ("free" in sub_lower and "pro" not in sub_lower and "power" not in sub_lower):
                        self.after(0, lambda i2=iid: self.acc_tree.set(i2, "overage", "unsupported"))
                        skipped += 1
                        self.after(0, lambda v=i: self.acc_progress.configure(value=v))
                        continue

                    access_token, err = get_valid_token(row, self.conn)
                    if not access_token:
                        self.after(0, lambda i2=iid, e=email, er=err:
                            self._log(f"{e}: TokenError {er}", "error"))
                        self.after(0, lambda i2=iid: self.acc_tree.set(i2, "overage", "TokenError"))
                        failed += 1
                        self.after(0, lambda v=i: self.acc_progress.configure(value=v))
                        continue

                    profile_arn = row["profileArn"] or FIXED_PROFILE_ARNS.get(provider, "")
                    if not profile_arn:
                        profile_arn = list_profiles(access_token) or ""
                    if not profile_arn:
                        self.after(0, lambda i2=iid: self.acc_tree.set(i2, "overage", "none ARN"))
                        failed += 1
                        self.after(0, lambda v=i: self.acc_progress.configure(value=v))
                        continue

                    result = enable_overage(access_token, profile_arn)
                    if result["ok"]:
                        db_update_usage(self.conn, row_id, {
                            "usageLimit": row["usageLimit"], "currentUsage": row["currentUsage"],
                            "overageCap": row["overageCap"], "currentOverages": row["currentOverages"],
                            "overageStatus": "ENABLED", "overageCharges": row["overageCharges"],
                        })
                        self.after(0, lambda i2=iid: self.acc_tree.set(i2, "overage", "ENABLED"))
                        success += 1
                    else:
                        err_data = result.get("error", {})
                        msg = translate_api_error(err_data)
                        if "unsupported" in msg or "FEATURE_NOT_SUPPORTED" in str(err_data):
                            self.after(0, lambda i2=iid: self.acc_tree.set(i2, "overage", "unsupported"))
                            skipped += 1
                        else:
                            self.after(0, lambda i2=iid, m=msg: self.acc_tree.set(i2, "overage", m[:10]))
                            self.after(0, lambda e=email, m=msg: self._log(f"{e}: {m}", "error"))
                            failed += 1

                    self.after(0, lambda v=i: self.acc_progress.configure(value=v))

                msg = f"Bulk overage completed: success {success}, Skip {skipped}, failed {failed}"
                self.after(0, lambda: self._log(msg, "success" if failed == 0 else "warn"))
                self.after(0, self._load_accounts_from_db)
            except Exception as exc:
                self.after(0, lambda e=str(exc): self._log(f"Bulk overage aborted abnormally: {e}", "error"))
            finally:
                self.running = False
        threading.Thread(target=_do, daemon=True).start()

    def _inject_selected(self):
        sel = self.acc_tree.selection()
        if not sel:
            messagebox.showwarning("Info", "Select the account you want to inject first")
            return
        row_id = int(sel[0])
        row = self.conn.execute("SELECT * FROM accounts WHERE id=?", (row_id,)).fetchone()
        if not row:
            return

        access_token, err = get_valid_token(row, self.conn)
        if access_token:
            row = self.conn.execute("SELECT * FROM accounts WHERE id=?", (row_id,)).fetchone()

        ok, msg = inject_account(row)
        if ok:
            self._log(f"{row['email']} - {msg}", "success")
            self._refresh_local_status()
        else:
            self._log(f"{row['email']} - {msg}", "error")

    def _inject_single(self, iid):
        row_id = int(iid)
        row = self.conn.execute("SELECT * FROM accounts WHERE id=?", (row_id,)).fetchone()
        if not row:
            return

        access_token, err = get_valid_token(row, self.conn)
        if access_token:
            row = self.conn.execute("SELECT * FROM accounts WHERE id=?", (row_id,)).fetchone()

        ok, msg = inject_account(row)
        if ok:
            self._log(f"{row['email']} - {msg}", "success")
            self._refresh_local_status()
        else:
            self._log(f"{row['email']} - {msg}", "error")

    # ─── Tab 2 Actions: Local status ─────────────────────────────────────────
    def _refresh_local_status(self):
        self.status_text.delete("1.0", "end")
        token = get_local_token_status()
        if not token:
            self.status_text.insert("end", "No local install detected Kiro Token\n\n", "expired")
            self.status_text.insert("end", f"Path: {KIRO_CACHE_DIR / 'kiro-auth-token.json'}\n")
            self.status_text.insert("end", "\nUse the 'Inject' action to write the account credentials")
            return

        fields = [
            ("Auth method", token.get("authMethod", "N/A")),
            ("Sign-in method", token.get("provider", "N/A")),
            ("Region", token.get("region", "N/A")),
            ("Expires at", token.get("expiresAt", "N/A")),
            ("ClientIdHash", token.get("clientIdHash", "N/A")),
            ("AccessToken", (token.get("accessToken", "")[:60] + "...") if token.get("accessToken") else "N/A"),
            ("RefreshToken", (token.get("refreshToken", "")[:60] + "...") if token.get("refreshToken") else "N/A"),
        ]

        for key, val in fields:
            self.status_text.insert("end", f"  {key:12s}: ", "key")
            self.status_text.insert("end", f"{val}\n", "val")

        self.status_text.insert("end", "\n")
        expires_at = token.get("expiresAt", "")
        if is_token_expired(expires_at):
            self.status_text.insert("end", "  Status: expired or near expiry\n", "expired")
            self.status_text.insert("end", "  Kiro On startup the RefreshToken is used to refresh automatically\n", "val")
        else:
            try:
                for fmt in ("%Y-%m-%dT%H:%M:%S.000Z", "%Y-%m-%d %H:%M:%S"):
                    try:
                        expires = datetime.strptime(expires_at, fmt)
                        break
                    except ValueError:
                        continue
                remaining = int((expires - datetime.now()).total_seconds())
                mins = remaining // 60
                self.status_text.insert("end", f"  Status: valid (remaining {mins} min)\n", "ok")
            except Exception:
                self.status_text.insert("end", "  Status: valid\n", "ok")

        client_hash = token.get("clientIdHash")
        if client_hash:
            client_path = KIRO_CACHE_DIR / f"{client_hash}.json"
            if client_path.exists():
                self.status_text.insert("end", f"\n  ClientReg: ", "key")
                self.status_text.insert("end", f"exists ({client_path.name})\n", "ok")
            else:
                self.status_text.insert("end", f"\n  ClientReg: ", "key")
                self.status_text.insert("end", "missing\n", "expired")

    def _refresh_local_token(self):
        token = get_local_token_status()
        if not token:
            messagebox.showwarning("Info", "No local token; inject an account first")
            return

        auth_method = token.get("authMethod", "")

        def _do():
            result = None
            if auth_method == "social":
                result = refresh_social_token(token.get("refreshToken", ""))
            elif auth_method == "IdC":
                client_hash = token.get("clientIdHash", "")
                client_path = KIRO_CACHE_DIR / f"{client_hash}.json"
                if not client_path.exists():
                    self.after(0, lambda: messagebox.showerror("Error", "clientRegistration file missing"))
                    return
                with open(client_path, "r", encoding="utf-8") as f:
                    client = json.load(f)
                region = token.get("region", "us-east-1")
                result = refresh_idc_token(
                    client["clientId"], client["clientSecret"],
                    token.get("refreshToken", ""), region
                )
            else:
                self.after(0, lambda: messagebox.showerror("Error", f"Unsupported authentication method: {auth_method}"))
                return

            if result:
                token["accessToken"] = result["accessToken"]
                token["refreshToken"] = result["refreshToken"]
                token["expiresAt"] = (datetime.now() + timedelta(seconds=result["expiresIn"])).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                save_data = {k: v for k, v in token.items() if k not in ("clientId", "clientSecret")}
                token_path = KIRO_CACHE_DIR / "kiro-auth-token.json"
                with open(token_path, "w", encoding="utf-8") as f:
                    json.dump(save_data, f, indent=2)
                self.after(0, self._refresh_local_status)
            else:
                self.after(0, lambda: messagebox.showerror("Error", "Token Refresh failed"))

        threading.Thread(target=_do, daemon=True).start()

    def _clear_local_token(self):
        if not messagebox.askyesno("Confirm", "Are you sure you want to clear the local Kiro token?\nAfter clearing, you'll need to re-inject or sign in again."):
            return
        token_path = KIRO_CACHE_DIR / "kiro-auth-token.json"
        if token_path.exists():
            token_path.unlink()
        self._refresh_local_status()


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()