"""
K.I.R.O Register - Batch/Service Mode

Headless runner for automated AWS Builder ID registration.
Supports batch mode (X accounts) and service mode (infinite loop).

Usage:
    python service.py --batch 10            # 10 accounts then stop
    python service.py --service             # Infinite loop until Ctrl+C
    python service.py --batch 50 --delay 30 # 50 accounts, 30s delay
"""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# Database configuration
DB_PATH = Path(os.getenv("DB_PATH", "kiro_accounts.db"))

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password TEXT DEFAULT '',
    provider TEXT DEFAULT '',
    authMethod TEXT DEFAULT '',
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
    """Initialize database connection and schema"""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(DB_SCHEMA)
    # Migration: add subscription/password columns if missing
    cols = [r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()]
    if "subscription" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN subscription TEXT DEFAULT ''")
    if "password" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN password TEXT DEFAULT ''")
    conn.commit()
    return conn


def save_account(result: dict):
    """Save successful registration to database"""
    try:
        conn = get_db()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        email = result.get("email")
        if not email:
            print("⚠️  No email in result, skipping DB save")
            return

        # Check if exists
        existing = conn.execute("SELECT id FROM accounts WHERE email=?", (email,)).fetchone()

        if existing:
            print(f"ℹ️  Account already exists in DB: {email}")
            conn.close()
            return

        # Insert new account
        conn.execute("""
            INSERT INTO accounts (
                email, provider, authMethod, accessToken, refreshToken,
                expiresAt, clientId, clientSecret, clientIdHash, region,
                profileArn, userId, subscription, updatedAt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            email,
            result.get("provider", "aws"),
            result.get("authMethod", "oauth"),
            result.get("accessToken", ""),
            result.get("refreshToken", ""),
            result.get("expiresAt", ""),
            result.get("clientId", ""),
            result.get("clientSecret", ""),
            result.get("clientIdHash", ""),
            result.get("region", "us-east-1"),
            result.get("profileArn", ""),
            result.get("userId", ""),
            result.get("subscription", ""),
            now
        ))

        conn.commit()
        conn.close()
        print(f"✓ Saved to database: {DB_PATH}")

    except Exception as e:
        print(f"⚠️  Failed to save to database: {e}")


# Import registration functions
try:
    from kiro_register import register, register_via_9router_oauth
    from mail_providers import get_provider as get_mail_provider, list_providers
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("ℹ️  Make sure you're running from the project root and dependencies are installed")
    print("ℹ️  Try: pip install -r requirements.txt")
    sys.exit(1)


def load_config():
    """Load configuration from kiro_config.json or CONFIG_PATH env var"""
    config_path = Path(os.getenv("CONFIG_PATH", "kiro_config.json"))

    # Generate default config from env vars if file doesn't exist
    if not config_path.exists():
        print(f"ℹ️  Config file not found: {config_path}")
        print("ℹ️  Generating config from environment variables...")

        config = {
            "mail_provider": os.getenv("MAIL_PROVIDER", "gsuite_imap"),
            "captcha_provider": os.getenv("CAPTCHA_PROVIDER", "multibot"),
            "proxy_url": os.getenv("PROXY_URL", ""),
            "router9_url": os.getenv("ROUTER9_URL", ""),
            "router9_password": os.getenv("ROUTER9_PASSWORD", ""),
        }

        # Mail provider specific config
        if config["mail_provider"] == "shiromail":
            config["shiromail"] = {
                "base_url": os.getenv("SHIROMAIL_BASE_URL", ""),
                "api_key": os.getenv("SHIROMAIL_API_KEY", ""),
                "domain_id": int(os.getenv("SHIROMAIL_DOMAIN_ID", "1"))
            }
        elif config["mail_provider"] == "yydsmail":
            config["yydsmail"] = {
                "base_url": os.getenv("YYDSMAIL_BASE_URL", ""),
                "api_key": os.getenv("YYDSMAIL_API_KEY", "")
            }
        elif config["mail_provider"] == "gsuite_imap":
            config["imap_server"] = os.getenv("IMAP_SERVER", "imap.gmail.com")
            config["imap_port"] = os.getenv("IMAP_PORT", "993")
            config["imap_user"] = os.getenv("IMAP_USER", "")
            config["imap_pass"] = os.getenv("IMAP_PASS", "")
            config["imap_domains_file"] = os.getenv("DOMAINS_PATH", "domains.txt")

        # Captcha config
        if config["captcha_provider"] == "yescaptcha":
            config["yescaptcha_api_key"] = os.getenv("YESCAPTCHA_API_KEY", "")
        elif config["captcha_provider"] == "multibot":
            config["multibot_key"] = os.getenv("MULTIBOT_KEY", "")

        # Save generated config
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        print(f"✓ Generated config file: {config_path}")

        return config

    print(f"ℹ️  Loading configuration from {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    provider = config.get("mail_provider", "not set")
    captcha = config.get("captcha_provider", "manual")
    proxy = "configured" if config.get("proxy_url") else "none"
    print(f"ℹ️  Mail provider: {provider}, Captcha: {captcha}, Proxy: {proxy}")

    return config


def get_mail_provider_instance(config):
    """Initialize mail provider from config with env var fallback."""
    import os

    provider_name = config.get("mail_provider", "shiromail")
    print(f"ℹ️  Initializing mail provider: {provider_name}")

    # Get provider config
    if provider_name == "shiromail":
        provider_config = config.get("shiromail", {})
    elif provider_name == "yydsmail":
        provider_config = config.get("yydsmail", {})
    elif provider_name == "gsuite_imap":
        # Check both nested and flat config structure
        provider_config = config.get("gsuite_imap", {})

        # Fallback to flat config (root level keys)
        if not provider_config:
            provider_config = {
                "imap_user": config.get("imap_user", ""),
                "imap_pass": config.get("imap_pass", ""),
                "imap_server": config.get("imap_server", "imap.gmail.com"),
                "imap_port": int(config.get("imap_port", 993)),
                "local_prefix": config.get("imap_local_prefix", "aws"),
                "local_length": int(config.get("imap_local_length", 10)),
            }

        # Env var fallback
        provider_config.setdefault("imap_user", os.getenv("GSUITE_IMAP_EMAIL", ""))
        provider_config.setdefault("imap_pass", os.getenv("GSUITE_IMAP_PASSWORD", ""))
        provider_config.setdefault("imap_server", os.getenv("GSUITE_IMAP_SERVER", "imap.gmail.com"))
        provider_config.setdefault("imap_port", int(os.getenv("GSUITE_IMAP_PORT", "993")))

        user = provider_config.get("imap_user", "")
        has_pass = bool(provider_config.get("imap_pass"))
        print(f"ℹ️  IMAP credentials: user={user}, pass={'***' if has_pass else 'MISSING'}")
    else:
        print(f"❌ Unknown mail provider: {provider_name}")
        print(f"ℹ️  Available providers: shiromail, yydsmail, gsuite_imap")
        return None

    try:
        print(f"ℹ️  Connecting to {provider_name} service...")
        result = get_mail_provider(provider_name, **provider_config)
        print(f"✅ Mail provider initialized successfully")
        return result
    except Exception as e:
        print(f"❌ Failed to initialize mail provider: {e}")
        print(f"ℹ️  Check your API keys and credentials in kiro_config.json")
        return None


def register_one_account(config, use_9router, headless):
    """Register a single account with retry logic (fresh email, reuse device_code)."""
    mail_provider = get_mail_provider_instance(config)
    if not mail_provider:
        print("❌ Failed to initialize mail provider")
        return None

    # Cache device_code only (not email)
    cached_device_code_data = None

    MAX_RETRY = 3
    for attempt in range(1, MAX_RETRY + 1):
        print(f"\n{'='*60}")
        print(f"🔄 Registration attempt {attempt}/{MAX_RETRY} (fresh email)")
        print(f"{'='*60}")

        try:
            if use_9router:
                # Transform flat config to nested structure
                router9_config = {
                    "base_url": config.get("router9_url"),
                    "password": config.get("router9_password"),
                    "auth_token": config.get("router9_auth_token"),
                    "auth_token_expires_at": config.get("router9_auth_token_expires_at")
                }

                if not router9_config.get("base_url") or not router9_config.get("password"):
                    print("❌ 9router config incomplete: missing base_url or password")
                    print("ℹ️  Add 'router9_url' and 'router9_password' to kiro_config.json")
                    return None

                print("ℹ️  Using 9router OAuth device code flow...")
                if cached_device_code_data:
                    expires_at_timestamp = cached_device_code_data.get("expires_at_timestamp", 0)
                    remaining = max(0, int(expires_at_timestamp - time.time()))
                    if remaining > 250:
                        print(f"ℹ️  Reusing cached device code (expires in {remaining}s)")
                    else:
                        print("⚠️  Cached device code expired, generating fresh one")
                        cached_device_code_data = None

                result = asyncio.run(register_via_9router_oauth(
                    mail_provider_instance=mail_provider,
                    router9_config=router9_config,
                    headless=headless,
                    proxy_url=config.get("proxy_url"),
                    auto_login=True,
                    skip_onboard=True,
                    cancel_check=None,
                    cached_email=None,  # Always fresh
                    cached_device_code_data=cached_device_code_data
                ))
            else:
                print("ℹ️  Using standard registration flow...")
                result = asyncio.run(register(
                    mail_provider_instance=mail_provider,
                    headless=headless,
                    proxy_url=config.get("proxy_url"),
                    auto_login=True,
                    skip_onboard=True,
                    cancel_check=None
                ))

            if result and result.get("email"):
                # Cache device_code for next retry (but not email)
                cached_device_code_data = result.get("device_code_info")

                # Check if incomplete (TES block, export failure, etc.)
                is_incomplete = result.get("incomplete", False)
                if is_incomplete:
                    fail_reason = result.get("failReason", "Unknown")
                    print(f"❌ Registration incomplete: {fail_reason}")

                    if "TES" in fail_reason or "Blocked" in fail_reason:
                        print("⚠️  AWS Trust Evaluation Service (TES) block detected")
                        print("ℹ️  Consider using residential proxy or reducing velocity")

                    if "device code" in fail_reason.lower() and "expired" in fail_reason.lower():
                        print("⚠️  9router device code expired - re-authentication needed")
                        cached_device_code_data = None  # Clear expired device code

                    if attempt < MAX_RETRY:
                        # TES blocks need longer cooling (synced with main.py)
                        is_tes_block = "TES" in fail_reason or "Blocked" in fail_reason
                        retry_delay = (10 + (attempt * 20)) if is_tes_block else (5 + (attempt * 3))
                        print(f"⏳ Waiting {retry_delay}s before retry with fresh email (reason: {'TES cooldown' if is_tes_block else 'standard backoff'})...")
                        time.sleep(retry_delay)
                    continue

                # Success - account complete and healthy
                email = result["email"]
                exported = result.get("router9_exported", False)
                print(f"✅ Registration successful!")
                print(f"   Email: {email}")
                if use_9router:
                    export_status = 'Yes ✅' if exported else 'No ⚠️'
                    print(f"   Exported to 9router: {export_status}")

                # Save to database
                save_account(result)

                return result
            else:
                # No result or no email
                print(f"❌ Registration failed: No result returned from registration flow")
                if attempt < MAX_RETRY:
                    print(f"⏳ Waiting 5s before retry (attempt {attempt + 1}/{MAX_RETRY})...")
                    time.sleep(5)

        except Exception as e:
            print(f"❌ Registration error: {e}")
            import traceback
            print(f"ℹ️  Stack trace: {traceback.format_exc()}")
            if attempt < MAX_RETRY:
                print(f"⏳ Waiting 5s before retry (attempt {attempt + 1}/{MAX_RETRY})...")
                time.sleep(5)

    # All retries exhausted
    print(f"❌ Registration failed after {MAX_RETRY} attempts")
    print(f"ℹ️  Check config, proxy settings, and try again later")
    return None


def print_stats(mode, target, successful, failed, total):
    """Print statistics summary."""
    print("\n" + "=" * 70)
    print(f"  {mode} COMPLETE")
    print("=" * 70)
    if target:
        print(f"  Target: {target}")
    print(f"  Total: {total}")
    print(f"  ✅ Success: {successful}")
    print(f"  ❌ Failed: {failed}")
    print(f"  Ended: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)


def run_batch(count, delay, use_9router, headless):
    """Batch mode - register N accounts then stop."""
    config = load_config()

    print("\n" + "=" * 70)
    print("  K.I.R.O BATCH MODE")
    print("=" * 70)
    print(f"  Target: {count} accounts")
    print(f"  Delay: {delay}s")
    print(f"  Flow: {'9router OAuth' if use_9router else 'Standard'}")
    print(f"  Browser: {'Headless' if headless else 'Headed'}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    successful = 0
    failed = 0

    for i in range(1, count + 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{count}] Starting registration...")
        print(f"{'='*60}")

        result = register_one_account(config, use_9router, headless)

        if result:
            successful += 1
            print(f"\n✅ Account {i}/{count} registered successfully")
        else:
            failed += 1
            print(f"\n❌ Account {i}/{count} registration failed")

        if i < count:
            print(f"\n⏳ Waiting {delay}s before next registration...")
            time.sleep(delay)

    print_stats("BATCH", count, successful, failed, count)


def run_service(delay, use_9router, headless):
    """Service mode - infinite loop until Ctrl+C."""
    config = load_config()

    print("\n" + "=" * 70)
    print("  K.I.R.O SERVICE MODE")
    print("=" * 70)
    print(f"  Mode: Continuous (Ctrl+C to stop)")
    print(f"  Delay: {delay}s")
    print(f"  Flow: {'9router OAuth' if use_9router else 'Standard'}")
    print(f"  Browser: {'Headless' if headless else 'Headed'}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    successful = 0
    failed = 0
    total = 0

    try:
        while True:
            total += 1
            print(f"\n{'='*60}")
            print(f"[Account #{total}] Starting registration...")
            print(f"Progress: ✅ {successful} | ❌ {failed}")
            print(f"{'='*60}")

            result = register_one_account(config, use_9router, headless)

            if result:
                successful += 1
                print(f"\n✅ Account #{total} registered successfully")
            else:
                failed += 1
                print(f"\n❌ Account #{total} registration failed")

            print(f"\n⏳ Waiting {delay}s before next registration...")
            time.sleep(delay)
            print_stats("SERVICE", None, successful, failed, total)

    except KeyboardInterrupt:
        print("\n\n⚠️  Service mode stopped by user (Ctrl+C)")
        print(f"ℹ️  Final statistics:")
        print_stats("SERVICE", None, successful, failed, total)


def run_batch_loop(count, account_delay, batch_delay, use_9router, headless):
    """Batch loop mode - register N accounts per batch, wait, repeat infinitely."""
    config = load_config()

    print("\n" + "=" * 70)
    print("  K.I.R.O BATCH LOOP MODE")
    print("=" * 70)
    print(f"  Accounts per batch: {count}")
    print(f"  Account delay: {account_delay}s")
    print(f"  Batch delay: {batch_delay}s")
    print(f"  Flow: {'9router OAuth' if use_9router else 'Standard'}")
    print(f"  Browser: {'Headless' if headless else 'Headed'}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    batch_num = 0
    total_successful = 0
    total_failed = 0
    total_accounts = 0

    try:
        while True:
            batch_num += 1
            batch_successful = 0
            batch_failed = 0

            print(f"\n{'=' * 70}")
            print(f"  BATCH #{batch_num}")
            print(f"{'=' * 70}")

            for i in range(1, count + 1):
                total_accounts += 1
                print(f"\n{'='*60}")
                print(f"[Batch {batch_num}, Account {i}/{count}] Starting registration...")
                print(f"{'='*60}")

                result = register_one_account(config, use_9router, headless)

                if result:
                    batch_successful += 1
                    total_successful += 1
                    print(f"\n✅ Batch {batch_num}, Account {i}/{count} registered successfully")
                else:
                    batch_failed += 1
                    total_failed += 1
                    print(f"\n❌ Batch {batch_num}, Account {i}/{count} registration failed")

                if i < count:
                    print(f"\n⏳ Account delay {account_delay}s before next account...")
                    time.sleep(account_delay)

            print(f"\n{'='*60}")
            print(f"✅ Batch #{batch_num} complete")
            print(f"   Batch: ✅ {batch_successful} | ❌ {batch_failed}")
            print(f"   Total: ✅ {total_successful} | ❌ {total_failed}")
            print(f"{'='*60}")

            print(f"\n⏳ Batch delay {batch_delay}s before next batch...")
            time.sleep(batch_delay)

    except KeyboardInterrupt:
        print("\n\n⚠️  Batch loop mode stopped by user (Ctrl+C)")
        print_stats("BATCH LOOP", None, total_successful, total_failed, total_accounts)
        print(f"  Total batches: {batch_num}")


def main():
    parser = argparse.ArgumentParser(
        description="K.I.R.O Register - Batch/Service Mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python service.py --batch 10
  python service.py --service --delay 60
  python service.py --batch-loop 5 --account-delay 10 --batch-delay 120
  python service.py --batch 20 --9router --headed
        """
    )

    # Mode selection
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--batch", type=int, metavar="N",
                            help="Batch mode: register N accounts then stop")
    mode_group.add_argument("--service", action="store_true",
                            help="Service mode: infinite loop until Ctrl+C")
    mode_group.add_argument("--batch-loop", type=int, metavar="N",
                            help="Batch loop mode: register N accounts per batch, repeat infinitely")

    # Common options
    parser.add_argument("--delay", type=int, default=10,
                        help="Delay in seconds between accounts (default: 10)")
    parser.add_argument("--account-delay", type=int, default=10,
                        help="Delay between accounts in batch-loop mode (default: 10)")
    parser.add_argument("--batch-delay", type=int, default=60,
                        help="Delay between batches in batch-loop mode (default: 60)")
    parser.add_argument("--9router", action="store_true",
                        help="Use 9router OAuth flow instead of standard registration")
    parser.add_argument("--headed", action="store_true",
                        help="Run browser in headed mode (visible)")

    args = parser.parse_args()

    headless = not args.headed
    use_9router = getattr(args, '9router')

    # Run the selected mode
    if args.batch:
        run_batch(args.batch, args.delay, use_9router, headless)
    elif args.service:
        run_service(args.delay, use_9router, headless)
    elif args.batch_loop:
        run_batch_loop(args.batch_loop, args.account_delay, args.batch_delay, use_9router, headless)


if __name__ == "__main__":
    main()
