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
import sys
import time
from datetime import datetime
from pathlib import Path

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
    """Load configuration from kiro_config.json"""
    config_path = Path("kiro_config.json")
    if not config_path.exists():
        print("❌ Config file not found: kiro_config.json")
        print("ℹ️  Please create kiro_config.json or run main.py GUI first")
        sys.exit(1)

    print("ℹ️  Loading configuration from kiro_config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    provider = config.get("mail_provider", "not set")
    captcha = config.get("captcha_provider", "manual")
    proxy = "configured" if config.get("proxy_url") else "none"
    print(f"ℹ️  Mail provider: {provider}, Captcha: {captcha}, Proxy: {proxy}")

    return config


def get_mail_provider_instance(config):
    """Initialize mail provider from config."""
    provider_name = config.get("mail_provider", "shiromail")
    print(f"ℹ️  Initializing mail provider: {provider_name}")

    # Get provider config
    if provider_name == "shiromail":
        provider_config = config.get("shiromail", {})
    elif provider_name == "yydsmail":
        provider_config = config.get("yydsmail", {})
    elif provider_name == "gsuite_imap":
        provider_config = config.get("gsuite_imap", {})
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
    """Register a single account with retry logic (synced with main.py)."""
    mail_provider = get_mail_provider_instance(config)
    if not mail_provider:
        print("❌ Failed to initialize mail provider")
        return None

    MAX_RETRY = 3
    for attempt in range(1, MAX_RETRY + 1):
        print(f"\n{'='*60}")
        print(f"🔄 Registration attempt {attempt}/{MAX_RETRY}")
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
                result = asyncio.run(register_via_9router_oauth(
                    mail_provider_instance=mail_provider,
                    router9_config=router9_config,
                    headless=headless,
                    proxy_url=config.get("proxy_url"),
                    auto_login=True,
                    skip_onboard=True,
                    cancel_check=None
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

                    if attempt < MAX_RETRY:
                        # TES blocks need longer cooling (synced with main.py)
                        is_tes_block = "TES" in fail_reason or "Blocked" in fail_reason
                        retry_delay = (10 + (attempt * 20)) if is_tes_block else (5 + (attempt * 3))
                        print(f"⏳ Waiting {retry_delay}s before retry (reason: {'TES cooldown' if is_tes_block else 'standard backoff'})...")
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
