"""
Stripe Checkout automation
- Redeems an EFunCard virtual credit card
- Fills out the Stripe Checkout form
- Handles invisible hCaptcha and 3DS verification
"""
import asyncio
import json
import os
import random
import time
import requests
import urllib3
urllib3.disable_warnings()
from datetime import datetime
from playwright.async_api import async_playwright
from captcha_solver import solve_hcaptcha

EFUNCARD_API = "https://card.efuncard.com/api/external"
EFUNCARD_TOKEN = "b352d13f20462ed46cff0aa417065496bd811eb8396b2e2fee11aeacb796fc00"


def log(msg, level='info'):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] [{level.upper():5s}] {msg}')


def efun_redeem(code, log=log):
    """Redeem a CDK code and return the resulting virtual-card details."""
    try:
        resp = requests.post(
            f"{EFUNCARD_API}/redeem",
            headers={
                "Authorization": f"Bearer {EFUNCARD_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"code": code},
            timeout=(10, 90),
            verify=False,
        )
        data = resp.json()
        if data.get("success"):
            card = data["data"]
            log(f"Card redeemed: *{card['lastFour']} ({card['status']})", "ok")
            log(f"  Valid until: {card.get('autoCancelAt', 'N/A')}", "info")
            return card
        else:
            log(f"Redeem response: {data.get('error')}", "warn")
            return None
    except Exception as e:
        log(f"Redeem request failed: {e}", "warn")
        return None


def efun_query(code, log=log):
    """Look up an already-redeemed card by CDK code."""
    try:
        resp = requests.get(
            f"{EFUNCARD_API}/cards/query/{code}",
            headers={
                "Authorization": f"Bearer {EFUNCARD_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=30,
            verify=False,
        )
        data = resp.json()
        if data.get("success"):
            return data["data"]
        log(f"Query response: {data.get('error')}", "warn")
        return None
    except Exception as e:
        log(f"Query request failed: {e}", "warn")
        return None


def efun_3ds_verify(code, minutes=5, log=log):
    """Fetch the latest 3DS verification code for a card."""
    try:
        resp = requests.post(
            f"{EFUNCARD_API}/3ds/verify",
            headers={
                "Authorization": f"Bearer {EFUNCARD_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"code": code, "minutes": minutes},
            timeout=30,
            verify=False,
        )
        if resp.status_code != 200 or not resp.text.strip():
            log(f"3DS API returned unexpected response: HTTP {resp.status_code}, body='{resp.text[:100]}'", "warn")
            return None
        try:
            data = resp.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            log(f"3DS API returned non-JSON: '{resp.text[:100]}'", "warn")
            return None
        if data.get("success"):
            verifications = data["data"].get("verifications", [])
            if verifications:
                latest = verifications[0]
                log(f"3DS code: {latest['otp']} (merchant: {latest.get('merchant', 'N/A')})", "ok")
                return latest
            log("No 3DS code yet", "info")
            return None
        log(f"3DS query failed: {data.get('error')}", "error")
        return None
    except Exception as e:
        log(f"3DS query failed: {e}", "warn")
        return None


async def fill_stripe_checkout(payment_url, card_info, cdk_code, log=log, headless=True):
    """Fill out and submit the Stripe Checkout form (headless by default)."""
    card_number = card_info["cardNumber"]
    cvv = card_info["cvv"]
    expiry_month = str(card_info["expiryMonth"]).zfill(2)
    expiry_year = str(card_info["expiryYear"])[-2:]
    name_on_card = card_info.get("nameOnCard", "Amy Allen")
    billing_address = card_info.get("billingAddress", "") or card_info.get("nodeInstructions", "")

    addr_parts = [p.strip() for p in billing_address.split(",")]
    address_line1 = addr_parts[0] if len(addr_parts) > 0 else ""
    city = addr_parts[1] if len(addr_parts) > 1 else ""
    state = addr_parts[2] if len(addr_parts) > 2 else ""
    postal_code = addr_parts[3] if len(addr_parts) > 3 else ""
    country = addr_parts[4] if len(addr_parts) > 4 else "US"

    log(f"Card: *{card_number[-4:]}, expiry: {expiry_month}/{expiry_year}, name: {name_on_card}")
    log(f"Address: {address_line1}, {city}, {state} {postal_code}, {country}")

    browser = None
    try:
        async with async_playwright() as p:
            from playwright_stealth import Stealth
            from kiro_register import _random_fingerprint_config, _build_fingerprint_script

            fp = _random_fingerprint_config()
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-first-run",
                f"--window-size={fp['screen']['width']},{fp['screen']['height']}",
            ]
            if headless:
                launch_args += ["--no-sandbox", "--disable-gpu"]

            browser = await p.chromium.launch(
                headless=headless,
                args=launch_args,
            )
            context = await browser.new_context(
                viewport=fp["viewport"],
                screen=fp["screen"],
                locale=fp["locale"],
                timezone_id=fp["timezone"],
                user_agent=fp["user_agent"],
                color_scheme="light",
                device_scale_factor=fp["pixel_ratio"],
            )
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)
            await context.add_init_script(_build_fingerprint_script(fp))

            log("Loading Stripe checkout page...")
            try:
                await page.goto(payment_url, timeout=60000, wait_until="domcontentloaded")
            except Exception:
                log("Page load failed, retrying...", "warn")
                await asyncio.sleep(3)
                try:
                    await page.goto(payment_url, timeout=60000, wait_until="commit")
                except Exception:
                    log("Checkout page could not be loaded", "error")
                    await browser.close()
                    return {"ok": False, "status": "error", "message": "page load failed"}

            # Wait for the form to render
            try:
                await page.wait_for_selector("#cardNumber", timeout=30000)
            except Exception:
                log("Checkout form did not appear, link may have expired", "error")
                await browser.close()
                return {"ok": False, "status": "error", "message": "checkout form did not appear"}

            await asyncio.sleep(2)

            # Read the page total — abort if this isn't a $0 trial
            log("Trial check: reading the 'due today' amount...", "info")
            amount_value = None
            try:
                amount_text = await page.evaluate(r"""() => {
                    const body = document.body.innerText;
                    // Look for the amount on the line after "Total due today" / "due today"
                    const m = body.match(/(?:total due today|due today|amount due)\s*\n\s*\$([\d,.]+)/i);
                    if (m) return m[1];
                    // Or the amount on the line after "total"
                    const m2 = body.match(/\btotal\b\s*\n\s*\$([\d,.]+)/i);
                    if (m2) return m2[1];
                    return '';
                }""")
                if amount_text:
                    amount_value = float(amount_text.replace(",", ""))
                    log(f"Page amount: ${amount_text} (due today)", "info")
                    if amount_value > 0:
                        log(f"Not a $0 trial (${amount_text}), aborting payment", "error")
                        await browser.close()
                        return {"ok": False, "status": "not_free_trial",
                                "message": f"due today is ${amount_text}, not a free trial"}
                    else:
                        log("Due today is $0.00, confirmed free trial", "info")
                else:
                    log("Could not detect a 'Total due today' amount, continuing...", "warn")
            except Exception as e:
                log(f"Amount check failed: {e}", "warn")

            # Country selector
            log("Setting country: United States")
            try:
                country_sel = page.locator("#billingCountry")
                if await country_sel.count() > 0:
                    await country_sel.select_option("US")
                    await asyncio.sleep(random.uniform(0.8, 1.5))
            except Exception:
                pass

            async def _stripe_move(loc):
                try:
                    box = await loc.bounding_box()
                    if box:
                        x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
                        y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                        await page.mouse.move(x, y, steps=random.randint(5, 12))
                        await asyncio.sleep(random.uniform(0.1, 0.3))
                except Exception:
                    pass

            async def _stripe_type(loc, text, delay_range=(40, 110)):
                await _stripe_move(loc)
                await loc.click()
                await asyncio.sleep(random.uniform(0.2, 0.5))
                await loc.fill("")
                for i, ch in enumerate(text):
                    await page.keyboard.type(ch, delay=0)
                    d = random.uniform(delay_range[0], delay_range[1]) / 1000
                    if random.random() < 0.06:
                        d += random.uniform(0.15, 0.4)
                    await asyncio.sleep(d)
                await asyncio.sleep(random.uniform(0.4, 0.9))

            # Card number
            log("Filling card number...")
            card_input = page.locator("#cardNumber")
            await _stripe_type(card_input, card_number, (45, 100))

            # Expiry
            log("Filling expiry...")
            expiry_input = page.locator("#cardExpiry")
            await _stripe_type(expiry_input, f"{expiry_month}{expiry_year}", (50, 120))

            # CVV
            log("Filling CVV...")
            cvc_input = page.locator("#cardCvc")
            await _stripe_type(cvc_input, cvv, (60, 140))

            # Cardholder name
            log("Filling cardholder name...")
            name_input = page.locator("#billingName")
            await _stripe_type(name_input, name_on_card, (35, 90))

            # Address
            log("Filling billing address...")
            try:
                addr_input = page.locator("#billingAddressLine1")
                await _stripe_type(addr_input, address_line1, (30, 80))
            except Exception:
                pass

            try:
                postal_input = page.locator("#billingPostalCode")
                if await postal_input.count() > 0 and await postal_input.is_visible():
                    await _stripe_type(postal_input, postal_code, (50, 120))
            except Exception:
                pass

            try:
                city_input = page.locator("#billingLocality")
                if await city_input.count() > 0 and await city_input.is_visible():
                    await _stripe_type(city_input, city, (35, 90))
            except Exception:
                pass

            try:
                state_select = page.locator("#billingAdministrativeArea")
                if await state_select.count() > 0 and await state_select.is_visible():
                    try:
                        await state_select.select_option(state.strip())
                    except Exception:
                        try:
                            await state_select.fill(state.strip())
                        except Exception:
                            pass
                    await asyncio.sleep(0.2)
            except Exception:
                pass

            log("Form filled, preparing to submit...", "ok")
            await asyncio.sleep(random.uniform(1.5, 3.0))

            # Click Subscribe
            log("Clicking Subscribe...")
            submit_btn = page.locator('button[type="submit"]')
            if await submit_btn.count() > 0:
                await _stripe_move(submit_btn)
                await asyncio.sleep(random.uniform(0.3, 0.8))
                await submit_btn.click()
            else:
                log("Submit button not found", "error")
                return {"ok": False, "status": "error", "message": "submit button not found"}

            # Wait for a result
            log("Waiting for payment result...")
            result = await _wait_for_payment_result(page, cdk_code, log)

            await browser.close()
            browser = None
            return result

    except Exception as e:
        err_msg = str(e)
        if "Target" in err_msg and "closed" in err_msg:
            log("Browser closed unexpectedly, payment interrupted", "error")
        elif "Timeout" in err_msg:
            log("Operation timed out", "error")
        else:
            log(f"Payment flow failed: {err_msg[:100]}", "error")
        return {"ok": False, "status": "error", "message": err_msg[:100]}
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass


async def _wait_for_payment_result(page, cdk_code, log, timeout=120):
    """Wait for the payment outcome, handling hCaptcha and 3DS along the way."""
    start = time.time()

    while time.time() - start < timeout:
        await asyncio.sleep(3)

        try:
            # Make sure the page is still alive
            current_url = page.url
        except Exception:
            log("Page closed", "error")
            return {"ok": False, "status": "error", "message": "page closed unexpectedly"}

        if "success" in current_url or "return_url" in current_url:
            log("Payment successful! Page redirected", "ok")
            return {"ok": True, "status": "success", "url": current_url}

        try:
            page_text = await page.evaluate("() => document.body.innerText")
        except Exception:
            page_text = ""

        if "thank you" in page_text.lower() or "subscription active" in page_text.lower():
            log("Payment successful! Confirmation detected", "ok")
            return {"ok": True, "status": "success", "message": "subscription confirmed"}

        # hCaptcha detection
        try:
            hcaptcha_visible = await page.evaluate("""() => {
                const iframe = document.querySelector('iframe[src*="hcaptcha.com/captcha"]');
                if (iframe && iframe.offsetWidth > 50 && iframe.offsetHeight > 50) return true;
                const challenge = document.querySelector('[data-hcaptcha-widget-id]');
                if (challenge && challenge.offsetWidth > 50) return true;
                return false;
            }""")
        except Exception:
            continue

        if hcaptcha_visible:
            log("hCaptcha detected, invoking YesCaptcha solver...", "warn")
            try:
                solved = await solve_hcaptcha(page, log_fn=log)
                if solved:
                    log("hCaptcha solved!", "ok")
                else:
                    log("hCaptcha solve failed", "error")
                    return {"ok": False, "status": "error", "message": "hCaptcha solve failed"}
            except Exception:
                log("hCaptcha handler crashed", "error")
            continue

        # 3DS detection
        try:
            is_3ds = await page.evaluate("""() => {
                const iframes = Array.from(document.querySelectorAll('iframe'));
                for (const f of iframes) {
                    if (f.src && (f.src.includes('3ds') || f.src.includes('acs') ||
                        f.src.includes('authenticate') || f.src.includes('challenge'))) {
                        return f.offsetWidth > 50;
                    }
                }
                const overlay = document.querySelector('[class*="3ds"], [class*="challenge"], [id*="3ds"]');
                return overlay && overlay.offsetWidth > 50;
            }""")
        except Exception:
            continue

        if is_3ds:
            log("3DS challenge detected!", "warn")
            try:
                await _handle_3ds(page, cdk_code, log)
            except Exception:
                log("3DS handler crashed", "error")
            continue

        # Surface error messages
        try:
            error_msg = await page.evaluate("""() => {
                const err = document.querySelector('[class*="error"], [class*="Error"], [role="alert"]');
                return err ? err.innerText.trim() : '';
            }""")
            if error_msg and len(error_msg) > 5:
                log(f"Payment error: {error_msg}", "error")
                return {"ok": False, "status": "error", "message": error_msg}
        except Exception:
            pass

        # Submit-button state
        try:
            btn_text = await page.evaluate("""() => {
                const btn = document.querySelector('button[type="submit"]');
                return btn ? btn.innerText.trim() : '';
            }""")
            if "processing" in btn_text.lower():
                log("Processing...", "dbg")
        except Exception:
            pass

    log("Payment timed out", "error")
    return {"ok": False, "status": "timeout"}


async def _handle_3ds(page, cdk_code, log):
    """Handle 3DS — pull the OTP from the EFunCard API and fill it in."""
    log("Fetching 3DS verification code...", "info")

    # Poll for the 3DS code
    for attempt in range(10):
        await asyncio.sleep(5)
        verification = efun_3ds_verify(cdk_code, minutes=5, log=log)
        if verification:
            otp = verification["otp"]
            log(f"3DS OTP retrieved: {otp}", "ok")

            # Try filling the code into a 3DS iframe
            frames = page.frames
            for frame in frames:
                if frame == page.main_frame:
                    continue
                try:
                    otp_input = frame.locator('input[type="text"], input[type="tel"], input[name*="otp"], input[name*="code"], input[placeholder*="code"]')
                    if await otp_input.count() > 0:
                        await otp_input.first.fill(otp)
                        log("3DS code filled", "ok")
                        await asyncio.sleep(1)

                        # Submit
                        submit = frame.locator('button[type="submit"], input[type="submit"], button:has-text("Submit"), button:has-text("Verify")')
                        if await submit.count() > 0:
                            await submit.first.click()
                            log("3DS verification submitted", "ok")
                        return
                except Exception:
                    continue

            # No iframe input found — try the main page
            try:
                otp_input = page.locator('input[name*="otp"], input[name*="code"], input[autocomplete*="one-time"]')
                if await otp_input.count() > 0:
                    await otp_input.first.fill(otp)
                    submit = page.locator('button[type="submit"]')
                    if await submit.count() > 0:
                        await submit.first.click()
                    log("3DS code filled and submitted on the main page", "ok")
                    return
            except Exception:
                pass

            log("3DS input field not found, waiting for manual handling...", "warn")
            return

    log("3DS verification code timeout", "error")


async def auto_pay(payment_url, cdk_code, gemini_key=None, captcha_config=None, headless=True, log=log):
    """
    Full auto-payment flow:
    1. Redeem / look up the virtual credit card
    2. Fill the Stripe form
    3. Handle verifications and submit (hCaptcha + 3DS)

    captcha_config: dict with keys:
        - yescaptcha_key: YesCaptcha API key
        - multibot_key:   Multibot API key
        - provider:       'yescaptcha' (default) or 'multibot'
    """
    if captcha_config:
        if captcha_config.get("yescaptcha_key"):
            os.environ["YESCAPTCHA_API_KEY"] = captcha_config["yescaptcha_key"]
        if captcha_config.get("multibot_key"):
            os.environ["MULTIBOT_API_KEY"] = captcha_config["multibot_key"]
        if captcha_config.get("provider"):
            os.environ["CAPTCHA_PROVIDER"] = captcha_config["provider"]
        if captcha_config.get("api_key"):
            os.environ["CAPTCHA_API_KEY"] = captcha_config["api_key"]
    elif gemini_key:
        os.environ["CAPTCHA_API_KEY"] = gemini_key

    log("=" * 50, "ok")
    log("Starting auto-payment flow", "info")
    log("=" * 50, "ok")

    # Step 1: get the card details
    # First, look up an existing redemption — avoid double-redeeming a code
    log("Querying virtual-card status...")
    card_info = efun_query(cdk_code, log)

    if card_info and card_info.get("cardNumber") and card_info.get("status") == "ACTIVE":
        log(f"Card already active: *{card_info.get('lastFour', '????')}", "ok")
    else:
        # Not redeemed or not active — try redeeming
        log("Card not ready, attempting to redeem...")
        card_info = None
        for retry in range(3):
            card_info = efun_redeem(cdk_code, log)
            if card_info and card_info.get("cardNumber"):
                break
            if retry < 2:
                log("Redeem returned no card info, waiting 10s before re-querying...", "info")
                time.sleep(10)
                card_info = efun_query(cdk_code, log)
                if card_info and card_info.get("cardNumber"):
                    break

        # Poll until the card is provisioned
        if not card_info or not card_info.get("cardNumber"):
            log("Polling for card provisioning...", "info")
            for attempt in range(18):
                time.sleep(10)
                log(f"Querying card... ({(attempt+1)*10}s)", "info")
                card_info = efun_query(cdk_code, log)
                if card_info and card_info.get("cardNumber"):
                    break
            if not card_info or not card_info.get("cardNumber"):
                log("Card provisioning timed out, no card info!", "error")
                return None

        # Wait for activation
        if card_info.get("status") and card_info["status"] != "ACTIVE":
            log(f"Card status: {card_info['status']}, waiting for activation...", "info")
            for attempt in range(12):
                time.sleep(5)
                card_info = efun_query(cdk_code, log)
                if card_info and card_info.get("status") == "ACTIVE":
                    log("Card activated!", "ok")
                    break
            else:
                if not card_info or card_info.get("status") != "ACTIVE":
                    log(f"Card never activated: {card_info.get('status') if card_info else 'None'}", "error")
                    return None

    # Step 2: fill and submit
    result = await fill_stripe_checkout(payment_url, card_info, cdk_code, log, headless=headless)

    log("=" * 50, "ok")
    if result and result.get("ok"):
        log("Payment flow complete!", "ok")
    else:
        log(f"Payment flow ended: {result}", "warn")
    log("=" * 50, "ok")

    return result


if __name__ == "__main__":
    import sys

    payment_url = sys.argv[1] if len(sys.argv) > 1 else 'https://checkout.stripe.com/c/pay/cs_live_b1F9f90pytQAzHaZSbHvc3xUeqcLAWaRrPEI9O7gQrwP8NZJzLOXKww0TO#fidnandhYHdWcXxpYCc%2FJ2FgY2RwaXEnKSd2cGd2ZndsdXFsamtQa2x0cGBrYHZ2QGtkZ2lgYSc%2FcXdwYCknYnBkZmRoamlgU2R3bGRrcSc%2FJ2Zqa3F3amknKSdkdWxOYHwnPyd1blppbHNgWjA0V2pEUlJMTVBtcmFAa3dRRn1MSX9pYWlof3YyQURkf2o0bzdSTWhAT1J0X0NxZzFkYW5cN2dUcTNVTG41dmxJMTRtbG1OSlV2QXZuT300XU9zVUFUZE9dNTVkZFFoUzNBNScpJ2N3amhWYHdzYHcnP3F3cGApJ2dkZm5id2pwa2FGamlqdyc%2FJyY1YzVjNDUnKSdpZHxqcHFRfHVgJz8naHBpcWxabHFgaCcpJ2BrZGdpYFVpZGZgbWppYWB3dic%2FcXdwYHgl'
    cdk_code = sys.argv[2] if len(sys.argv) > 2 else "US-QV8Q4-CDEHM-GY7TU-PMDMR-R2JSA"

    captcha_cfg = {
        "yescaptcha_key": os.environ.get("YESCAPTCHA_API_KEY", ""),
        "multibot_key": os.environ.get("MULTIBOT_API_KEY", ""),
        "provider": os.environ.get("CAPTCHA_PROVIDER", "yescaptcha"),
    }

    asyncio.run(auto_pay(payment_url, cdk_code, captcha_config=captcha_cfg, headless=True))
