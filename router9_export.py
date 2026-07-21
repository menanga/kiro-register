#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
9router Integration Module
Automatically exports Kiro accounts to 9router after registration.

Supports both local and remote 9router instances:
- Local: http://localhost:20128
- Remote: https://xapi.fastev.my.id
- LAN: http://192.168.1.100:20128
"""
import json
import ssl
import urllib.request
import urllib.error
from typing import Optional, Dict, List


class Router9Exporter:
    """Export Kiro refresh tokens to 9router (local or remote)."""

    def __init__(self, base_url: str = "http://localhost:20128", verify_ssl: bool = True):
        """
        Initialize the exporter.

        Args:
            base_url: 9router base URL (supports http/https, local/remote)
                Examples:
                - http://localhost:20128 (local)
                - https://xapi.fastev.my.id (remote HTTPS)
                - http://192.168.1.100:20128 (LAN)
            verify_ssl: Verify SSL certificates (default: True)
                Set to False for self-signed certs (development only)
        """
        self.base_url = base_url.rstrip("/")
        self.import_endpoint = f"{self.base_url}/api/oauth/kiro/import"
        self.verify_ssl = verify_ssl

        # Create SSL context for HTTPS requests
        if not verify_ssl and base_url.startswith("https"):
            self.ssl_context = ssl.create_default_context()
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE
        else:
            self.ssl_context = None

    def check_connection(self) -> bool:
        """
        Check if 9router is running and accessible.

        Returns:
            True if 9router is accessible, False otherwise
        """
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/health",
                method="GET"
            )
            # Use SSL context for HTTPS with self-signed certs
            kwargs = {"timeout": 5}
            if self.ssl_context:
                kwargs["context"] = self.ssl_context
            with urllib.request.urlopen(req, **kwargs) as resp:
                return resp.status == 200
        except (urllib.error.URLError, urllib.error.HTTPError, Exception):
            return False

    def export_single_token(self, refresh_token: str, client_id: str = "", client_secret: str = "", log=print) -> Dict:
        """
        Export a single refresh token to 9router.

        Args:
            refresh_token: The Kiro refresh token to export
            log: Logging callback

        Returns:
            Dict with status and result
        """
        if not refresh_token:
            return {"ok": False, "error": "Empty refresh token"}

        try:
            body = {
                "refreshToken": refresh_token,
                "clientId": client_id,
                "clientSecret": client_secret
            }
            req = urllib.request.Request(
                self.import_endpoint,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )

            # Use SSL context for HTTPS with self-signed certs
            kwargs = {"timeout": 15}
            if self.ssl_context:
                kwargs["context"] = self.ssl_context

            with urllib.request.urlopen(req, **kwargs) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                log(f"9router import success: {result.get('imported', [])}", "ok")
                return {"ok": True, "data": result}

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            try:
                error_data = json.loads(error_body)
            except Exception:
                error_data = {"raw": error_body}
            log(f"9router import failed: HTTP {e.code} - {error_data}", "err")
            return {"ok": False, "error": error_data, "status": e.code}

        except Exception as e:
            log(f"9router connection error: {e}", "err")
            return {"ok": False, "error": str(e)}

    def export_bulk_tokens(self, refresh_tokens: List[str], log=print) -> Dict:
        """
        Export multiple refresh tokens to 9router in one request.

        Args:
            refresh_tokens: List of Kiro refresh tokens
            log: Logging callback

        Returns:
            Dict with status and result
        """
        if not refresh_tokens:
            return {"ok": False, "error": "Empty token list"}

        # Filter out empty tokens
        tokens = [t.strip() for t in refresh_tokens if t.strip()]
        if not tokens:
            return {"ok": False, "error": "No valid tokens"}

        try:
            body = {"refreshTokens": tokens}
            req = urllib.request.Request(
                self.import_endpoint,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )

            # Use SSL context for HTTPS with self-signed certs
            kwargs = {"timeout": 15}
            if self.ssl_context:
                kwargs["context"] = self.ssl_context

            with urllib.request.urlopen(req, **kwargs) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                imported = result.get("imported", [])
                failed = result.get("failed", [])
                log(f"9router bulk import: {len(imported)} succeeded, {len(failed)} failed", "ok")
                if failed:
                    log(f"Failed tokens: {[f.get('error', 'unknown') for f in failed]}", "warn")
                return {"ok": True, "data": result}

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            try:
                error_data = json.loads(error_body)
            except Exception:
                error_data = {"raw": error_body}
            log(f"9router bulk import failed: HTTP {e.code} - {error_data}", "err")
            return {"ok": False, "error": error_data, "status": e.code}

        except Exception as e:
            log(f"9router connection error: {e}", "err")
            return {"ok": False, "error": str(e)}

    def export_account_result(self, registration_result: Dict, log=print) -> Dict:
        """
        Export a registration result to 9router.

        Args:
            registration_result: Dict returned from kiro_register.register()
            log: Logging callback

        Returns:
            Dict with status and result
        """
        refresh_token = registration_result.get("refreshToken", "")
        if not refresh_token:
            log("No refresh token in registration result; skipping 9router export", "warn")
            return {"ok": False, "error": "No refresh token"}

        client_id = registration_result.get("clientId", "")
        client_secret = registration_result.get("clientSecret", "")
        email = registration_result.get("email", "unknown")
        log(f"Exporting {email} to 9router...", "info")

        result = self.export_single_token(refresh_token, client_id, client_secret, log)

        if result["ok"]:
            log(f"✓ {email} successfully exported to 9router", "ok")
        else:
            log(f"✗ Failed to export {email} to 9router", "err")

        return result


def export_from_database(db_path: str = "kiro_accounts.db",
                        base_url: str = "http://localhost:20128",
                        log=print) -> Dict:
    """
    Export all accounts from the database to 9router.

    Args:
        db_path: Path to the SQLite database
        base_url: 9router base URL
        log: Logging callback

    Returns:
        Dict with export statistics
    """
    import sqlite3
    from pathlib import Path

    if not Path(db_path).exists():
        log(f"Database not found: {db_path}", "err")
        return {"ok": False, "error": "Database not found"}

    exporter = Router9Exporter(base_url)

    # Check 9router connection
    if not exporter.check_connection():
        log("9router is not running or not accessible", "err")
        log(f"Make sure 9router is running at {base_url}", "info")
        log("Install: npm install -g 9router", "info")
        log("Run: 9router", "info")
        return {"ok": False, "error": "9router not accessible"}

    log("9router connection OK", "ok")

    # Read all accounts from database
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT email, refreshToken, clientId, clientSecret FROM accounts WHERE refreshToken != ''").fetchall()
    conn.close()

    if not rows:
        log("No accounts with refresh tokens found in database", "warn")
        return {"ok": True, "exported": 0, "failed": 0}

    log(f"Found {len(rows)} accounts to export", "info")

    # Export each account individually with client credentials
    exported_count = 0
    failed_count = 0

    for row in rows:
        email = row["email"]
        refresh_token = row["refreshToken"]
        client_id = row.get("clientId", "")
        client_secret = row.get("clientSecret", "")

        log(f"Exporting {email}...", "info")
        result = exporter.export_single_token(refresh_token, client_id, client_secret, log)

        if result["ok"]:
            exported_count += 1
        else:
            failed_count += 1

    log(f"Export complete: {exported_count} imported, {failed_count} failed", "ok")
    return {"ok": True, "exported": exported_count, "failed": failed_count}


if __name__ == "__main__":
    import sys

    def simple_log(msg, level="info"):
        prefix = {
            "ok": "[✓]",
            "err": "[✗]",
            "warn": "[!]",
            "info": "[*]"
        }.get(level, "[?]")
        print(f"{prefix} {msg}")

    # Test connection
    exporter = Router9Exporter()
    if exporter.check_connection():
        simple_log("9router is running", "ok")

        # Export from database
        if len(sys.argv) > 1:
            db_path = sys.argv[1]
        else:
            db_path = "kiro_accounts.db"

        result = export_from_database(db_path, log=simple_log)
        sys.exit(0 if result["ok"] else 1)
    else:
        simple_log("9router is not running", "err")
        simple_log("Install: npm install -g 9router", "info")
        simple_log("Run: 9router", "info")
        sys.exit(1)
