# K.I.R.O Register - Development Guide

An automation toolkit for AWS Builder ID accounts — batch registration, token management, subscription automation, and health monitoring.

## Project Overview

This is a Python-based automation tool that orchestrates:
- Automated AWS Builder ID registration (headless browser via Playwright)
- Pluggable temp-mail backends (ShiroMail, YYDSMail, self-hosted IMAP)
- Token refresh and account state monitoring
- Pro subscription automation with Stripe integration
- Account health checks (ban detection, trial status)
- Anti-detection measures (randomized fingerprints, residential proxy support)

**Target Python Version:** 3.11+  
**Primary Frameworks:** Playwright (browser automation), Tkinter (GUI), curl_cffi (TLS fingerprinting)

## Quick Start

### Initial Setup

```bash
# Create and activate virtualenv
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser runtime
python -m playwright install chromium
```

### Running the Application

```bash
# Launch the GUI
python main.py
```

On first launch, the app generates `kiro_config.json` with mail provider settings, captcha API keys, and CDK codes.

## Development Commands

### Testing

```bash
# Run all tests
pytest

# Run with verbose output
python -m pytest -v

# Run specific test file
pytest tests/test_registration.py

# Run with coverage
pytest --cov=. --cov-report=html
```

### Code Quality

```bash
# Lint with ruff (fast, comprehensive)
ruff check .

# Auto-fix linting issues
ruff check --fix .

# Format code
ruff format .

# Type checking (if using mypy)
mypy .

# Alternative formatters
black .
```

### Build

```bash
# Build Windows executable (Windows only)
build.bat

# Or run PyInstaller directly
python build.py
```

## Project Structure

```
main.py                 # Tkinter GUI + account manager + main event loop
kiro_register.py        # Registration state machine (browser automation)
kiro_subscribe.py       # Subscription management API client
kiro_login.py           # Manual OAuth login helper (token capture)
stripe_pay.py           # Stripe Checkout automation via Playwright
captcha_solver.py       # hCaptcha solver (pluggable: YesCaptcha/Multibot)
roxy_register.py        # Registration via RoxyBrowser fingerprint profiles
build.py                # PyInstaller packaging driver
domains.txt             # Domain pool for self-hosted IMAP (one per line)
mail_providers/         # Pluggable temp-mail backends
  base.py              # Abstract MailProvider interface
  shiromail.py         # ShiroMail implementation
  yydsmail.py          # YYDSMail implementation
  gsuite_imap.py       # Self-hosted Gmail/Workspace catch-all
```

## Key Architecture Patterns

### 1. Registration State Machine

`kiro_register.py` orchestrates the multi-step registration flow:
1. Create temporary email via pluggable provider
2. Launch Playwright browser (optionally through residential proxy)
3. Navigate to AWS Builder ID registration
4. Handle hCaptcha (automated via YesCaptcha/Multibot or manual fallback)
5. Submit email and poll for OTP code
6. Complete registration and extract OAuth tokens
7. Encrypt and store tokens in SQLite

### 2. Mail Provider Plugin System

New temp-mail providers can be added by:
1. Subclassing `MailProvider` from `mail_providers/base.py`
2. Implementing `create_mailbox()`, `wait_otp()`, `list_domains()`
3. Registering in `mail_providers/__init__.py` `PROVIDERS` dict
4. Provider appears in GUI dropdown automatically

### 3. Proxy Architecture

Both curl_cffi HTTP sessions and Playwright browser contexts route through the configured residential proxy:
- Format: `http://user:pass@host:port`
- Configured in GUI Settings tab
- Persisted to `kiro_config.json`
- Helps avoid AWS Trust Evaluation Service (TES) blocks

### 4. Anti-Detection Layers

- **Browser fingerprinting**: Randomized user agents, viewport sizes, WebGL parameters
- **playwright-stealth**: Patches common bot detection signals
- **curl_cffi**: Mimics browser TLS fingerprints in non-browser HTTP calls
- **Residential proxies**: Avoids datacenter IP flagging

## Configuration File

`kiro_config.json` (auto-generated, gitignored):

```json
{
  "mail_provider": "shiromail",
  "shiromail": {
    "base_url": "https://api.shiromail.com",
    "api_key": "your-api-key",
    "domain_id": 1
  },
  "yydsmail": {
    "base_url": "https://api.yydsmail.com",
    "api_key": ""
  },
  "gsuite_imap": {
    "host": "imap.gmail.com",
    "port": 993,
    "user": "you@gmail.com",
    "password": "app-password",
    "domains_file": "domains.txt"
  },
  "yescaptcha_api_key": "",
  "multibot_key": "",
  "captcha_provider": "yescaptcha",
  "proxy_url": "http://user:pass@proxy.example.com:8080",
  "cdk_codes": []
}
```

## Common Development Workflows

### Adding a New Mail Provider

1. Create `mail_providers/newprovider.py`
2. Implement the MailProvider interface:
   ```python
   from .base import MailProvider
   
   class NewProvider(MailProvider):
       def create_mailbox(self, domain: str) -> tuple[str, str]:
           """Return (email, mailbox_id)"""
           pass
       
       def wait_otp(self, mailbox_id: str, timeout: int = 300) -> str:
           """Poll for 6-digit OTP code"""
           pass
       
       def list_domains(self) -> list[str]:
           """Return available domain names"""
           pass
   ```
3. Register in `mail_providers/__init__.py`:
   ```python
   from .newprovider import NewProvider
   
   PROVIDERS = {
       'shiromail': ShiroMail,
       'yydsmail': YYDSMail,
       'gsuite_imap': GsuiteIMAP,
       'newprovider': NewProvider,  # Add here
   }
   ```

### Testing Registration Flow

```bash
# Run with headed browser to watch the flow
python main.py
# In GUI: uncheck "Headless" checkbox, set concurrency to 1, click Start

# For debugging specific steps, use kiro_register.py directly:
python -c "
from kiro_register import register_account
from mail_providers import get_provider

provider = get_provider('shiromail', {'api_key': '...'})
result = register_account(provider, headless=False)
print(result)
"
```

### Debugging TES Blocks

AWS Trust Evaluation Service (TES) blocks show up as:
- `create-identity` API returns `400` with `errorCode=BLOCKED`
- Symptoms: registration hangs after email submission, OTP never validates

**Solutions**:
1. Configure a residential proxy in Settings tab
2. Disable headless mode (TES profiles headless Chrome)
3. Use self-hosted IMAP with owned domains (not temp-mail services)
4. Slow down concurrent registrations (TES tracks velocity)

### Database Schema

SQLite database `accounts.db` (created automatically):

```sql
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE,
    encrypted_token BLOB,
    refresh_token TEXT,
    expires_at TIMESTAMP,
    is_pro BOOLEAN,
    is_banned BOOLEAN,
    created_at TIMESTAMP,
    last_checked TIMESTAMP
);
```

Token encryption uses `cryptography.fernet` with a machine-specific key.

## Troubleshooting

### `playwright._impl._api_types.Error: Executable doesn't exist`
Run `python -m playwright install chromium`

### GUI crashes on Linux
Install Tkinter: `sudo apt install python3-tk`

### hCaptcha never solves
- Open Settings tab
- Paste YesCaptcha or Multibot API key
- Select active provider from dropdown
- Empty key = manual solve (GUI will pause and wait)

### `401` on subscription flow
Token expired or account banned. Check account status in Accounts tab and refresh token.

### Email OTP never arrives
- Mail provider unreachable or API key wrong
- Test in Mail Provider tab
- Check mail provider rate limits

### `create-identity -> 400 BLOCKED by TES`
AWS TES flagged the session. Configure residential proxy and retry with fresh account.

### OTP received but `INVALID_OTP` on every submit
First TES block consumed the OTP server-side. Retry loop stops after second `INVALID_OTP`. Fix: add residential proxy before next attempt.

## Dependencies

| Package | Purpose |
|---------|---------|
| `playwright>=1.50.0` | Headless browser automation (Chromium) |
| `playwright-stealth>=2.0.0` | Anti-bot detection patches |
| `curl_cffi>=0.15.0` | TLS fingerprint impersonation for HTTP calls |
| `requests>=2.31.0` | Standard HTTP client (non-fingerprinted calls) |
| `httpx>=0.27.0` | Async HTTP client |
| `cryptography>=42.0` | Token encryption at rest |
| `pyinstaller>=6.0` | Packaging into standalone executable |
| `tkinter` | Desktop GUI (bundled with Python) |

## Security & Legal Notice

**Educational and research purposes only.** This tool automates AWS Builder ID account management. Users are responsible for:
- Complying with AWS Terms of Service
- Ensuring legitimate use of automation
- Respecting rate limits and service policies
- Protecting API keys and credentials

Selling, redistributing, or using this tool for commercial purposes is prohibited. Use entirely at your own risk.

## Contributing

When contributing:
1. Follow PEP 8 style (enforced by ruff/black)
2. Add type hints to function signatures
3. Write docstrings for public functions
4. Add tests for new mail providers or registration steps
5. Update this CLAUDE.md if adding major features

## Resources

- [Original Project (Chinese)](https://github.com/GALIAIS/k_i_r_o-register)
- [Playwright Python Docs](https://playwright.dev/python/docs/intro)
- [AWS Builder ID](https://docs.aws.amazon.com/signin/latest/userguide/builder-id.html)
- [YesCaptcha API](https://yescaptcha.com/docs)
- [Multibot API](https://multibot.cloud/docs)
