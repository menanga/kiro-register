#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Headless K.I.R.O registration service for Docker / Dokploy.

All runtime settings can be supplied via environment variables or a mounted
`kiro_config.json`. Sensitive values (passwords, API keys) should be set in
Dokploy's "Environment" tab, not committed to the image.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import kiro_register
import mail_providers

logger = logging.getLogger("kiro-service")

DEFAULT_CFG = {
    "mail_provider": "gsuite_imap",
    "imap_server": "imap.gmail.com",
    "imap_port": 993,
    "imap_user": "",
    "imap_pass": "",
    "imap_folder": "INBOX",
    "imap_domains_file": "domains.txt",
    "captcha_provider": "multibot",
    "router9_url": "",
    "router9_password": "",
    "router9_auth_token": "",
    "router9_auth_token_expires_at": "",
    "proxy_url": "",
    "shiromail_base_url": "https://shiromail.galiais.com",
    "shiromail_api_key": "",
    "shiromail_domain_id": "",
    "yydsmail_base_url": "",
    "yydsmail_api_key": "",
    "yydsmail_domain": "",
    "yydsmail_subdomain": "",
    "yydsmail_wildcard": False,
}

ENV_MAP = {
    "MAIL_PROVIDER": "mail_provider",
    "IMAP_SERVER": "imap_server",
    "IMAP_PORT": "imap_port",
    "IMAP_USER": "imap_user",
    "IMAP_PASS": "imap_pass",
    "IMAP_FOLDER": "imap_folder",
    "IMAP_DOMAINS_FILE": "imap_domains_file",
    "DOMAINS_PATH": "imap_domains_file",
    "CAPTCHA_PROVIDER": "captcha_provider",
    "SHIROMAIL_BASE_URL": "shiromail_base_url",
    "SHIROMAIL_API_KEY": "shiromail_api_key",
    "SHIROMAIL_DOMAIN_ID": "shiromail_domain_id",
    "YYDSMAIL_BASE_URL": "yydsmail_base_url",
    "YYDSMAIL_API_KEY": "yydsmail_api_key",
    "YYDSMAIL_DOMAIN": "yydsmail_domain",
    "YYDSMAIL_SUBDOMAIN": "yydsmail_subdomain",
    "ROUTER9_URL": "router9_url",
    "ROUTER9_PASSWORD": "router9_password",
    "ROUTER9_AUTH_TOKEN": "router9_auth_token",
    "ROUTER9_AUTH_TOKEN_EXPIRES_AT": "router9_auth_token_expires_at",
    "PROXY_URL": "proxy_url",
}


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
            value = value.strip().strip('"\'')
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


def _env_bool(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_domains_path(cfg: dict, config_path: Path) -> Path:
    raw = cfg.get("imap_domains_file") or os.environ.get("DOMAINS_PATH") or "domains.txt"
    p = Path(raw)
    if not p.is_absolute():
        base = config_path.parent if config_path else Path.cwd()
        p = base / p
    return p


def load_configuration() -> tuple[dict, Path]:
    config_path = Path(os.environ.get("CONFIG_PATH", "/config/kiro_config.json"))
    cfg = dict(DEFAULT_CFG)
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except Exception as exc:
            logger.warning("Failed to read %s: %s", config_path, exc)

    for env_key, cfg_key in ENV_MAP.items():
        val = os.environ.get(env_key)
        if val is not None:
            cfg[cfg_key] = val

    domains_env = os.environ.get("DOMAINS", "").strip()
    domain_list: list[str] = []
    if domains_env:
        domain_list = [d.strip() for d in domains_env.split(",") if d.strip()]
        domains_path = _resolve_domains_path(cfg, config_path)
        try:
            domains_path.parent.mkdir(parents=True, exist_ok=True)
            domains_path.write_text("\n".join(domain_list) + "\n", encoding="utf-8")
        except OSError:
            # Config volume may be mounted read-only; the provider still receives the list.
            pass
    elif config_path.exists():
        domains_path = _resolve_domains_path(cfg, config_path)
        if domains_path.exists():
            try:
                domain_list = [
                    line.strip()
                    for line in domains_path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]
            except Exception:
                pass

    cfg["domains"] = domain_list
    return cfg, config_path


def _validate_proxy_url(url: str) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https", "socks5", "socks5h"}:
            raise ValueError(f"unsupported scheme {parsed.scheme!r}")
        if not parsed.hostname:
            raise ValueError("missing hostname")
        if parsed.port is None or not (1 <= parsed.port <= 65535):
            raise ValueError(f"invalid port {parsed.port!r}")
    except Exception as exc:
        logger.warning("Ignoring invalid PROXY_URL %r: %s", url, exc)
        return None
    return url


def _start_xvfb_if_needed(headless: bool) -> subprocess.Popen | None:
    """Start an in-container Xvfb display when running a headed browser in Docker."""
    if headless:
        return None
    if os.environ.get("DISPLAY"):
        return None
    try:
        proc = subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1920x1080x24", "-ac", "+extension", "GLX", "+render", "-noreset"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Give Xvfb a moment to come up before Playwright tries to use it.
        time.sleep(0.8)
        os.environ["DISPLAY"] = ":99"
        logger.info("Started Xvfb on DISPLAY :99 for headed browser")
        return proc
    except Exception as exc:
        logger.warning("Could not start Xvfb for headed browser: %s", exc)
        return None


def build_mail_provider(cfg: dict, config_path: Path):
    name = str(cfg.get("mail_provider", "gsuite_imap")).strip().lower()
    domain_list = cfg.get("domains") or []

    if name == "gsuite_imap":
        port = cfg.get("imap_port", 993)
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 993
        domains_path = _resolve_domains_path(cfg, config_path)
        return mail_providers.GsuiteImapProvider(
            imap_server=cfg.get("imap_server", "imap.gmail.com"),
            imap_port=port,
            imap_user=cfg.get("imap_user", ""),
            imap_pass=cfg.get("imap_pass", ""),
            imap_folder=cfg.get("imap_folder", "INBOX"),
            domains=domain_list or None,
            domains_file=str(domains_path) if domains_path.exists() and not domain_list else None,
            local_length=10,
        )

    if name == "shiromail":
        did = cfg.get("shiromail_domain_id", "")
        try:
            did = int(did)
        except (TypeError, ValueError):
            did = 0
        return mail_providers.ShiroMailProvider(
            base_url=cfg.get("shiromail_base_url", ""),
            api_key=cfg.get("shiromail_api_key", ""),
            domain_id=did,
        )

    if name == "yydsmail":
        return mail_providers.YydsMailProvider(
            api_key=cfg.get("yydsmail_api_key", ""),
            base_url=cfg.get("yydsmail_base_url", ""),
            domain=cfg.get("yydsmail_domain", ""),
            subdomain=cfg.get("yydsmail_subdomain", ""),
            wildcard=_env_bool(cfg.get("yydsmail_wildcard", False)),
        )

    raise ValueError(f"Unknown mail provider: {name}")


def log_callback(msg, level="info"):
    lvl = str(level).lower()
    if lvl in ("err", "error", "fail", "critical"):
        logger.error(msg)
    elif lvl in ("warn", "warning"):
        logger.warning(msg)
    elif lvl in ("ok", "success"):
        logger.info("[ok] %s", msg)
    else:
        logger.info(msg)


def _db_path() -> Path | None:
    raw = os.environ.get("DB_PATH", "/data/accounts.db")
    if not raw:
        return None
    return Path(raw)


def init_db():
    db = _db_path()
    if not db:
        return
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS service_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            password TEXT,
            full_name TEXT,
            provider TEXT,
            auth_method TEXT,
            region TEXT,
            client_id TEXT,
            client_secret TEXT,
            access_token TEXT,
            refresh_token TEXT,
            expires_at TEXT,
            incomplete INTEGER DEFAULT 0,
            fail_reason TEXT,
            router9_exported INTEGER DEFAULT 0,
            created_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def persist_account(result: dict):
    db = _db_path()
    if not db:
        return
    try:
        conn = sqlite3.connect(str(db))
        conn.execute(
            """
            INSERT INTO service_accounts
            (email, password, full_name, provider, auth_method, region, client_id,
             client_secret, access_token, refresh_token, expires_at, incomplete,
             fail_reason, router9_exported, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.get("email"),
                result.get("password"),
                result.get("full_name"),
                result.get("provider"),
                result.get("authMethod"),
                result.get("region"),
                result.get("clientId"),
                result.get("clientSecret"),
                result.get("accessToken"),
                result.get("refreshToken"),
                result.get("expiresAt"),
                1 if result.get("incomplete") else 0,
                result.get("failReason", ""),
                1 if result.get("router9_exported") else 0,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("Failed to persist account to DB")


async def run_account(cfg: dict, config_path: Path, use_9router: bool, proxy_url: str, headless: bool):
    provider = build_mail_provider(cfg, config_path)

    if use_9router:
        router9_config = {
            "base_url": cfg.get("router9_url", ""),
            "password": cfg.get("router9_password", ""),
            "auth_token": cfg.get("router9_auth_token", ""),
            "auth_token_expires_at": cfg.get("router9_auth_token_expires_at", ""),
        }
        if not router9_config["base_url"] or not router9_config["password"]:
            raise ValueError("router9_url and router9_password are required for --9router mode")
        result = await kiro_register.register_via_9router_oauth(
            headless=headless,
            auto_login=True,
            skip_onboard=True,
            mail_provider_instance=provider,
            router9_config=router9_config,
            proxy_url=proxy_url,
            log=log_callback,
        )
    else:
        result = await kiro_register.register(
            headless=headless,
            auto_login=True,
            skip_onboard=True,
            mail_provider_instance=provider,
            proxy_url=proxy_url,
            log=log_callback,
        )

    incomplete = bool(result.get("incomplete"))
    email = result.get("email", "N/A")
    if incomplete:
        logger.warning("Partial result for %s: %s", email, result.get("failReason"))
    else:
        logger.info("Success for %s", email)
    persist_account(result)
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="K.I.R.O registration service")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--service", action="store_true", help="Run forever, one account per cycle")
    mode.add_argument("--batch", type=int, default=0, help="Register N accounts then exit")
    mode.add_argument("--batch-loop", type=int, default=0, help="Register N accounts per batch, repeat forever")

    parser.add_argument("--delay", type=int, default=int(os.environ.get("DELAY", "300")), help="Seconds between accounts")
    parser.add_argument("--batch-delay", type=int, default=int(os.environ.get("BATCH_DELAY", "3600")), help="Seconds between batches in --batch-loop")
    parser.add_argument("--headless", action="store_true", default=None, help="Run browser headless (default in containers)")
    parser.add_argument("--no-headless", dest="headless", action="store_false", help="Show browser window")
    parser.add_argument("--9router", "--nine-router", dest="nine_router", action="store_true", help="Use 9router OAuth device-code flow")
    parser.add_argument("--proxy", default=os.environ.get("PROXY_URL", ""), help="Proxy URL, e.g. http://user:pass@host:port")
    parser.add_argument("--provider", default=os.environ.get("MAIL_PROVIDER", ""), help="Mail provider: gsuite_imap | shiromail | yydsmail")
    parser.add_argument("--config", default=os.environ.get("CONFIG_PATH", "/config/kiro_config.json"), help="Path to kiro_config.json")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    return parser.parse_args(argv)


async def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    os.environ.setdefault("APPDATA", "/data")
    if args.config:
        os.environ["CONFIG_PATH"] = args.config
    if args.provider:
        os.environ["MAIL_PROVIDER"] = args.provider
    if args.proxy:
        os.environ["PROXY_URL"] = args.proxy

    cfg, config_path = load_configuration()
    logger.info("Config path: %s", config_path)
    logger.info("Mail provider: %s", cfg.get("mail_provider"))
    logger.info("DB path: %s", _db_path())

    init_db()

    if args.headless is None:
        headless = os.environ.get("DISPLAY", "") == ""
    else:
        headless = args.headless

    proxy_url = _validate_proxy_url((args.proxy or cfg.get("proxy_url", "")).strip())
    xvfb_proc = _start_xvfb_if_needed(headless)

    if str(cfg.get("mail_provider", "")).strip().lower() == "gsuite_imap" and not cfg.get("domains"):
        domains_path = _resolve_domains_path(cfg, config_path)
        logger.error(
            "Gsuite/IMAP provider has an empty domain pool. "
            "Set DOMAINS=example.com,example.org or populate %s",
            domains_path,
        )
        sys.exit(1)

    if args.batch:
        count, loop_forever, inter_batch = args.batch, False, args.delay
    elif args.batch_loop:
        count, loop_forever, inter_batch = args.batch_loop, True, args.batch_delay
    elif args.service:
        count, loop_forever, inter_batch = 1, True, args.delay
    else:
        count, loop_forever, inter_batch = 1, False, args.delay

    try:
        batch_id = 0
        while True:
            batch_id += 1
            logger.info("Starting batch %d with %d account(s)", batch_id, count)
            for i in range(count):
                try:
                    await run_account(cfg, config_path, args.nine_router, proxy_url, headless)
                except Exception:
                    logger.exception("Account registration failed")
                if i < count - 1:
                    logger.info("Waiting %ds before next account", args.delay)
                    await asyncio.sleep(args.delay)

            if loop_forever:
                logger.info("Batch %d done; sleeping %ds before next cycle", batch_id, inter_batch)
                await asyncio.sleep(inter_batch)
            else:
                break
    finally:
        if xvfb_proc is not None:
            try:
                xvfb_proc.terminate()
                xvfb_proc.wait(timeout=5)
            except Exception:
                xvfb_proc.kill()

    logger.info("Service finished")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
