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

from kiro_register import register, register_via_9router_oauth
from mail_providers import get_provider


def load_config():
    """Load kiro_config.json or exit."""
    config_path = Path("kiro_config.json")
    if not config_path.exists():
        print("❌ Error: kiro_config.json not found")
        print("   Run the GUI first to generate config")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def print_stats(mode, count, successful, failed, total):
    """Print final statistics."""
    print("\n" + "=" * 70)
    print(f"  {mode} COMPLETE")
    print("=" * 70)
    print(f"  Processed: {total}")
    print(f"  Successful: {successful} ({successful/total*100:.1f}%)")
    print(f"  Failed: {failed}")
    print(f"  Ended: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)


def register_one(config, use_9router, headless, num, total=None):
    """Run single registration. Returns True if successful."""
    ts = datetime.now().strftime('%H:%M:%S')
    label = f"Account {num}/{total}" if total else f"Account #{num}"

    print(f"\n[{ts}] {label}")
    print("-" * 70)

    try:
        provider_name = config["mail_provider"]

        # Map flat config keys to provider constructor parameters
        if provider_name == "gsuite_imap":
            # Gmail/IMAP provider expects: imap_server, imap_port, imap_user, imap_pass, domains_file
            try:
                imap_port_int = int(config.get("imap_port", 993))
            except (ValueError, TypeError):
                imap_port_int = 993

            provider_config = {
                "imap_server": config.get("imap_server", "imap.gmail.com"),
                "imap_port": imap_port_int,
                "imap_user": config.get("imap_user", ""),
                "imap_pass": config.get("imap_pass", ""),
                "domains_file": config.get("imap_domains_file", "domains.txt"),
            }
        else:
            # For shiromail, yydsmail, etc., try to find nested config or use empty dict
            provider_config = config.get(provider_name, {})

        mail_provider = get_provider(provider_name, **provider_config)

        if use_9router:
            # Transform flat config to nested structure expected by registration function
            router9_config = {
                "base_url": config.get("router9_url"),
                "password": config.get("router9_password"),
                "auth_token": config.get("router9_auth_token"),
                "auth_token_expires_at": config.get("router9_auth_token_expires_at")
            }

            if not router9_config.get("base_url") or not router9_config.get("password"):
                print("❌ 9router config missing in kiro_config.json")
                print("   Required: router9_url, router9_password")
                return False

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
            result = asyncio.run(register(
                mail_provider_instance=mail_provider,
                headless=headless,
                proxy_url=config.get("proxy_url"),
                auto_login=True,
                skip_onboard=True,
                cancel_check=None
            ))

        if result and result.get("email"):
            email = result["email"]
            exported = result.get("router9_exported", False)
            print(f"✅ Success! Email: {email}")
            if use_9router:
                print(f"   Exported: {'Yes' if exported else 'No'}")
            return True
        else:
            error = result.get("error", "Unknown") if result else "No result"
            print(f"❌ Failed: {error}")
            return False

    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


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
        if register_one(config, use_9router, headless, i, count):
            successful += 1
        else:
            failed += 1

        if i < count:
            print(f"\n⏳ Waiting {delay}s...")
            time.sleep(delay)

    print_stats("BATCH", count, successful, failed, count)


def run_service(delay, use_9router, headless):
    """Service mode - register continuously until Ctrl+C."""
    config = load_config()

    print("\n" + "=" * 70)
    print("  K.I.R.O SERVICE MODE (INFINITE)")
    print("=" * 70)
    print(f"  Delay: {delay}s")
    print(f"  Flow: {'9router OAuth' if use_9router else 'Standard'}")
    print(f"  Browser: {'Headless' if headless else 'Headed'}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Stop: Press Ctrl+C")
    print("=" * 70)

    successful = 0
    failed = 0
    total = 0

    try:
        while True:
            total += 1
            if register_one(config, use_9router, headless, total, None):
                successful += 1
            else:
                failed += 1

            print(f"\n⏳ Waiting {delay}s...")
            time.sleep(delay)

    except KeyboardInterrupt:
        print("\n\n🛑 Stopped by user (Ctrl+C)")
        print_stats("SERVICE", None, successful, failed, total)


def main():
    parser = argparse.ArgumentParser(
        description="K.I.R.O Register - Batch/Service Mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python service.py --batch 10              # 10 accounts
  python service.py --service               # Infinite loop
  python service.py --batch 50 --delay 30   # 50 accounts, 30s delay
  python service.py --service --9router     # Service with 9router OAuth
  python service.py --batch 100 --headless  # Headless browser
        """
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--batch", type=int, metavar="N",
                     help="Batch mode: register N accounts then stop")
    mode.add_argument("--service", action="store_true",
                     help="Service mode: infinite loop until Ctrl+C")

    parser.add_argument("--delay", type=int, default=10, metavar="SEC",
                       help="Delay between registrations (default: 10s)")
    parser.add_argument("--9router", dest="use_9router", action="store_true",
                       help="Use 9router OAuth flow")
    parser.add_argument("--headless", action="store_true",
                       help="Run browser in headless mode")

    args = parser.parse_args()

    if args.delay < 1:
        print("❌ --delay must be at least 1 second")
        sys.exit(1)

    if args.batch and args.batch < 1:
        print("❌ --batch must be at least 1")
        sys.exit(1)

    try:
        if args.batch:
            run_batch(args.batch, args.delay, args.use_9router, args.headless)
        else:
            run_service(args.delay, args.use_9router, args.headless)
    except KeyboardInterrupt:
        print("\n\n🛑 Interrupted")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
