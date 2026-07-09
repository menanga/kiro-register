# K.I.R.O Register (English edition)

An automation toolkit for AWS Builder ID accounts — batch registration, token management, subscription automation, and health monitoring. English fork of [GALIAIS/k_i_r_o-register](https://github.com/GALIAIS/k_i_r_o-register).

> **Legal notice.** This project is for educational and research purposes only. Selling, redistributing, or using it for any commercial purpose is strictly prohibited. You use it entirely at your own risk.

---

## Features

- Automated registration flow (headless mode supported)
- Pluggable temp-mail backends (ShiroMail, YYDSMail) — easy to extend
- Automatic token refresh and account state monitoring
- Pro subscription automation (Stripe payment integration)
- Account health checks (ban detection, trial-status detection)
- Automatic retry on registration failure
- Randomized browser fingerprint and anti-detection
- Local SQLite database for account storage

## Requirements

- Python 3.11+ (the prebuilt Windows release targets 3.11)
- Windows, macOS, or Linux (the GUI uses Tkinter — bundled with CPython on Windows/macOS; on Linux you may need `sudo apt install python3-tk`)
- A working network connection (registration hits AWS OIDC endpoints)
- Optional: a [YesCaptcha](https://yescaptcha.com) or [Multibot](https://multibot.cloud) API key if you want automatic hCaptcha solving

## Installation

```bash
# Clone
git clone https://github.com/YOUR_FORK/k_i_r_o-register-en.git
cd k_i_r_o-register-en

# Create and activate a virtualenv (recommended)
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Install the Chromium runtime Playwright drives
python -m playwright install chromium
```

### Key Python dependencies

| Package | Purpose |
|---|---|
| `playwright` | Headless Chromium automation |
| `playwright-stealth` | Anti-bot detection patches |
| `curl_cffi` | TLS fingerprint impersonation |
| `requests` | Plain HTTP calls where TLS impersonation isn't needed |
| `cryptography` | Token encryption at rest |
| `tkinter` | Desktop GUI (bundled with Python; `apt install python3-tk` on Linux) |

## Usage Overview

This project offers two ways to run:

| Mode | Use Case | How to Run |
|------|----------|------------|
| **GUI** (`main.py`) | Interactive use, testing, manual control, viewing account details | `python main.py` |
| **Service** (`service.py`) | Automated batch registration, scheduled runs, headless servers, Docker | `python service.py --batch 10` |

**Choose GUI if:** You want to interactively configure settings, test mail providers, monitor progress visually, or manage existing accounts.

**Choose Service if:** You want automated batch registration, continuous operation, scheduled runs via cron/systemd, or deployment in Docker containers.

---

## Running the GUI (`main.py`)

### Launch the GUI
```bash
python main.py
```

A desktop window opens with multiple tabs. On first launch, the app generates `kiro_config.json` — your mail provider settings, API keys, and proxy configuration are stored here (gitignored).

### Step-by-Step: First Run Setup

#### 1. Configure Mail Provider (Required)
Navigate to the **Mail Provider** tab:

- **For paid temp-mail services (ShiroMail/YYDSMail):**
  1. Select provider from dropdown
  2. Enter API base URL and API key
  3. Click **Refresh Domains** to load available domains
  4. Select a domain from the dropdown
  5. Click **Test** to verify the connection

- **For self-hosted Gmail/IMAP catch-all (recommended):**
  1. Select **Gsuite/IMAP (self-hosted)**
  2. Fill IMAP settings:
     - Host: `imap.gmail.com`
     - Port: `993`
     - User: your Gmail address
     - Pass: [Google App Password](https://support.google.com/accounts/answer/185833) (not your regular password)
     - Domains file: path to `domains.txt` (one domain per line)
  3. Click **Test** to verify IMAP connection

#### 2. Configure Captcha (Optional)
Navigate to the **Captcha** tab:

- Without a captcha API key, the tool uses **manual solving** — registration will pause and wait for you to solve the captcha in the browser window
- For automatic solving:
  1. Get an API key from [YesCaptcha](https://yescaptcha.com) or [Multibot](https://multibot.cloud)
  2. Paste the key into the appropriate field
  3. Select the active provider from the dropdown
  4. The tool will now solve captchas automatically

#### 3. Start Registration
Navigate to the **Registration** tab:

1. **Concurrency:** Set how many accounts to register in parallel (1-5 recommended; higher = faster but more likely to trigger TES blocks)
2. **Headless mode:** Check this to hide browser windows (faster, uses less memory)
3. **9router OAuth flow:** Check this to use 9router OAuth flow with automatic export
4. Click **Start** — progress streams into the log pane
5. Click **Stop** to gracefully halt registration

#### 4. Manage Accounts
Navigate to the **Accounts** tab:

- View all registered accounts with their email, token status, expiry, and ban state
- **Refresh token:** Right-click an account → Refresh
- **Delete account:** Right-click an account → Delete
- **Export accounts:** Use the toolbar to export account data

#### 5. Configure Proxy (Recommended)
Navigate to the **Settings** tab:

- Paste a residential proxy URL: `http://user:pass@host:port`
- This routes browser traffic through the proxy to avoid AWS TES blocks
- Without a proxy, datacenter IPs are more likely to be flagged
- Proxy setting is saved to `kiro_config.json`

### Self-hosted mail with Gsuite/IMAP catch-all

If you own a pool of domains whose MX records catch-all forward into a Gmail or Workspace inbox, you can skip paid temp-mail services entirely:

1. Point every domain's MX to Gmail/Workspace (`ASPMX.L.GOOGLE.COM` chain) or use Cloudflare Email Routing with a catch-all rule to a Gmail address.
2. In the receiving account, enable IMAP and generate an app password (Google → Account → 2FA → App passwords).
3. List your domains in `domains.txt` next to `main.py` (one per line). A ready-made list of 27 sample `.tech` domains ships in the repo — replace it with your own.
4. In the GUI pick *Gsuite/IMAP (self-hosted)* and fill IMAP host (`imap.gmail.com`), port (`993`), user (`you@gmail.com`), pass (the app password), and the path to `domains.txt`.

Registration then invents a unique `<random>@<random-domain>` alias per account, polls the inbox by `To:` header, and extracts the 6-digit OTP automatically.

## Headless Batch/Service Mode (`service.py`)

For automated registration without the GUI, use `service.py` — a headless runner that supports three modes:

### Batch Mode
Register exactly N accounts then stop:
```bash
python service.py --batch 10 --delay 300 --headless --9router
```

### Batch-Loop Mode (NEW!)
Register N accounts per batch, wait, then repeat infinitely — perfect for continuous registration with rate limiting:
```bash
python service.py --batch-loop 10 --delay 300 --batch-delay 3600 --headless --9router
```
- `--batch-loop 10` — 10 accounts per batch
- `--delay 300` — 5 minutes between each account
- `--batch-delay 3600` — 1 hour between batches (default)

Press `Ctrl+C` to stop. Statistics are displayed.

### Service Mode
Register continuously until `Ctrl+C`:
```bash
python service.py --service --delay 300 --headless --9router
```

### Command Reference

**Required (choose one):**
- `--batch N` — Register N accounts then stop
- `--batch-loop N` — Register N accounts per batch, repeat infinitely
- `--service` — Register continuously

**Optional:**
- `--delay SEC` — Delay between account registrations (default: 10s)
- `--batch-delay SEC` — Delay between batches in batch-loop mode (default: 3600s)
- `--9router` — Use 9router OAuth flow for automatic export
- `--headless` — Run browser in headless mode (recommended for automation)

## Docker Support

Run the headless service in a container:

### Build and run
```bash
# Build image
docker-compose build

# Batch-loop mode: 10 accounts/batch, 5min delays, 1hr between batches
docker-compose run --rm kiro-service python service.py \
  --batch-loop 10 \
  --delay 300 \
  --batch-delay 3600 \
  --headless \
  --9router

# Batch mode: 10 accounts then stop
docker-compose run --rm kiro-service python service.py --batch 10 --delay 300 --headless --9router

# Service mode: infinite loop
docker-compose run --rm kiro-service python service.py --service --delay 300 --headless --9router
```

The container mounts your project directory, so `kiro_config.json`, `accounts.db`, and `domains.txt` are read from and written to your local filesystem.

**Note:** The GUI (main.py) requires X11/VNC setup in Docker, which adds significant complexity. Recommended approach: run the GUI locally, use Docker only for headless service mode.

## Packaging a Windows .exe

The repo ships a PyInstaller build script and a matching `build.bat`:

```cmd
:: From a Windows shell with Python on PATH
build.bat
```

The output lands in `dist\KiroProManager\KiroProManager.exe`. The `build.py` script bundles the Playwright Chromium runtime, `curl_cffi` native libs, and the `mail_providers` package so the exe is self-contained.

GitHub Actions also builds a release zip on every `v*` tag — see `.github/workflows/build.yml`.

## Project layout

```
main.py              # Tkinter GUI + account manager
kiro_register.py     # Registration state machine
kiro_subscribe.py    # Subscription management API
kiro_login.py        # Manual login helper (OAuth token capture)
stripe_pay.py        # Stripe Checkout automation
captcha_solver.py    # hCaptcha solver (pluggable: YesCaptcha or Multibot)
roxy_register.py     # Registration via RoxyBrowser fingerprint profiles
build.py             # PyInstaller packaging driver
build.bat            # Windows one-shot build entry point
domains.txt          # Domain pool used by the gsuite_imap provider (one per line)
mail_providers/      # Pluggable temp-mail backends (shiromail, yydsmail, gsuite_imap)
  base.py            # Abstract MailProvider base class
  shiromail.py       # ShiroMail implementation
  yydsmail.py        # YYDSMail implementation
```

## Adding a new mail provider

1. Create `mail_providers/myprovider.py`
2. Subclass `MailProvider` from `base.py`
3. Implement `create_mailbox`, `wait_otp`, `list_domains`
4. Register it in `mail_providers/__init__.py`'s `PROVIDERS` dict
5. It shows up in the GUI's provider dropdown automatically

## Configuration file (`kiro_config.json`)

Auto-generated on first run. Example:

```json
{
  "mail_provider": "shiromail",
  "shiromail": {
    "base_url": "https://example.com",
    "api_key": "...",
    "domain_id": 1
  },
  "yescaptcha_api_key": "",
  "multibot_key": "",
  "captcha_provider": "yescaptcha",
  "cdk_codes": []
}
```

## Captcha providers

The hCaptcha solver is pluggable. Pick one at runtime via either the GUI dropdown or the `CAPTCHA_PROVIDER` env var:

| Provider | Env var for the key | Endpoint | Notes |
|---|---|---|---|
| `yescaptcha` (default) | `YESCAPTCHA_API_KEY` | `https://api.yescaptcha.com` | JSON API; simpler pricing tiers |
| `multibot` | `MULTIBOT_API_KEY` | `https://api.multibot.cloud` | Cheaper per-solve; classic 2captcha-style API |

Both keys can be stored simultaneously — only the provider selected in `CAPTCHA_PROVIDER` / the GUI dropdown is used for the active run.

## Residential proxy (recommended for TES-heavy flows)

AWS ships a Trust Evaluation Service (TES) in front of `signin.aws` / `profile.aws`. Suspicious IPs get the `create-identity` API blocked with `errorCode=BLOCKED`, and since OTPs are consumed server-side, a first-shot block burns the whole account.

To avoid getting flagged, drive the browser through a residential proxy:

1. Grab a proxy URL from any residential provider (DataImpulse, IPRoyal, Bright Data, Smartproxy, etc.). Format: `http://user:pass@host:port`.
2. Open the **Settings** tab and paste the URL into the **Proxy URL** field. It's persisted into `kiro_config.json`.
3. Leave **Headless** unchecked — TES also profiles headless Chrome.

Both the inner `curl_cffi` session (OIDC client registration, token exchange) and Playwright's Chromium launch are routed through the configured proxy.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `playwright._impl._api_types.Error: Executable doesn't exist` | Run `python -m playwright install chromium` |
| GUI opens but crashes immediately on Linux | Missing Tk: `sudo apt install python3-tk` |
| hCaptcha never solves | Open Settings, paste a key for either YesCaptcha or Multibot, then pick the active provider in the dropdown — empty key = manual solve |
| `401` on subscription flow | Token expired or account banned. Inspect the account row in the Accounts tab |
| Registration hangs on email OTP | Mail provider unreachable or API key wrong — test it in the Mail provider tab |
| `create-identity -> 400 BLOCKED by TES` | AWS TES flagged the session. Configure a residential proxy (above) and retry with a fresh account. |
| OTP received but `INVALID_OTP` on every submit | The first TES block consumed the OTP server-side; the retry loop now short-circuits on the second `INVALID_OTP` so you don't keep burning retries. Root fix is the same: add a proxy. |

## Credits

Original project by [GALIAIS](https://github.com/GALIAIS) and the [LINUX DO community](https://linux.do). This fork is a straight English translation of strings, comments, and docs — runtime behaviour is intentionally unchanged.
