# Kiro Registration Flow Guide

Complete guide to understanding and using the automated AWS Builder ID registration system.

## Table of Contents

- [Overview](#overview)
- [Registration Flow](#registration-flow)
- [GUI Checkbox Options](#gui-checkbox-options)
- [Step-by-Step Process](#step-by-step-process)
- [Troubleshooting](#troubleshooting)
- [9router Integration](#9router-integration)

---

## Overview

This tool automates the creation and management of AWS Builder ID accounts for use with Kiro (Amazon's AI assistant). The registration process involves:

1. **Email Creation**: Generate temporary email addresses via pluggable providers
2. **Browser Automation**: Use Playwright to navigate AWS registration pages
3. **Captcha Solving**: Automatically solve hCaptcha challenges (with API key) or manual fallback
4. **OTP Retrieval**: Poll temp mail for verification code
5. **Token Extraction**: Capture OAuth tokens (refresh token, client credentials)
6. **Local Storage**: Encrypt and store tokens in SQLite database
7. **Optional Features**: Auto-login, Pro subscription, 9router export

**Security Note**: All tokens are encrypted at rest using machine-specific keys. Client credentials (clientId, clientSecret) are required for token refresh operations.

---

## Registration Flow

### Visual Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. INITIALIZATION                                               │
│    • Load configuration (mail provider, captcha keys, proxy)    │
│    • Generate random user profile (name, fingerprint)           │
│    • Select temporary email domain                              │
└────────────────────┬────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────────────┐
│ 2. EMAIL CREATION                                               │
│    • Call mail provider API (ShiroMail/YYDSMail/IMAP)          │
│    • Receive email address + mailbox ID                         │
│    Example: test123@shiromail.com                              │
└────────────────────┬────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────────────┐
│ 3. BROWSER LAUNCH                                               │
│    • Launch Chromium via Playwright                             │
│    • Apply anti-detection patches (playwright-stealth)          │
│    • Set randomized fingerprint:                                │
│      - User agent (Chrome 130-133)                             │
│      - Viewport size (1280x800 to 1920x1080)                   │
│      - WebGL renderer (NVIDIA/AMD/Intel)                        │
│      - Timezone (US/Canada/UK)                                  │
│      - Locale (en-US, en-GB, etc.)                             │
│    • Configure residential proxy (if enabled)                   │
└────────────────────┬────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────────────┐
│ 4. AWS BUILDER ID REGISTRATION                                  │
│    • Navigate to signin.aws.amazon.com                          │
│    • Click "Create AWS Builder ID"                              │
│    • Fill in:                                                   │
│      - Email: [temp mail address]                              │
│      - Name: [random generated name]                           │
│      - Confirm checkbox                                         │
└────────────────────┬────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────────────┐
│ 5. hCAPTCHA SOLVING                                             │
│    • Detect hCaptcha challenge                                  │
│    • If API key configured:                                     │
│      - Submit to YesCaptcha/Multibot service                   │
│      - Poll for solution (30-60 seconds)                        │
│      - Inject solution token                                    │
│    • If no API key:                                             │
│      - Pause automation                                         │
│      - User manually solves captcha                             │
│      - Resume after detection                                   │
└────────────────────┬────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────────────┐
│ 6. EMAIL VERIFICATION                                           │
│    • Submit registration form                                   │
│    • Poll mail provider for OTP code (6 digits)                │
│    • Timeout: 5 minutes                                         │
│    • Extract code from email body                               │
│    Example: "Your verification code is 123456"                 │
└────────────────────┬────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────────────┐
│ 7. OTP SUBMISSION                                               │
│    • Enter 6-digit code in AWS form                            │
│    • Submit verification                                        │
│    • Handle errors:                                             │
│      - INVALID_OTP: Retry once (TES may consume first attempt) │
│      - TES BLOCKED: Stop and report (need proxy)               │
└────────────────────┬────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────────────┐
│ 8. OAUTH TOKEN CAPTURE                                          │
│    • Intercept OAuth callback (localhost:3128)                  │
│    • Extract authorization code from URL                        │
│    • Exchange code for tokens via AWS OIDC API:                │
│      - POST /token with code                                    │
│      - Receive:                                                 │
│        * accessToken (expires in 1 hour)                       │
│        * refreshToken (long-lived, ~90 days)                   │
│        * clientId (OAuth app identifier)                       │
│        * clientSecret (OAuth app credential)                   │
└────────────────────┬────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────────────┐
│ 9. TOKEN STORAGE                                                │
│    • Encrypt tokens with machine-specific key                   │
│    • Store in SQLite database (kiro_accounts.db):              │
│      - email, refreshToken, clientId, clientSecret             │
│      - expires_at, is_pro, is_banned                           │
│      - created_at, last_checked                                │
│    • Also write to ~/.aws/sso/cache/ for Kiro desktop pickup  │
└────────────────────┬────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────────────┐
│ 10. POST-REGISTRATION (Optional Checkboxes)                     │
│    ┌──────────────────────────────────────────────────────┐   │
│    │ □ Auto Sign-In: Launch Kiro desktop app              │   │
│    │   • Start app with --no-sandbox flag                 │   │
│    │   • Kiro detects token in ~/.aws/sso/cache/         │   │
│    └──────────────────────────────────────────────────────┘   │
│    ┌──────────────────────────────────────────────────────┐   │
│    │ □ Skip Onboarding: Bypass welcome screens            │   │
│    │   • Modify Kiro's storage.json                       │   │
│    │   • Set onboardingCompleted: true                    │   │
│    └──────────────────────────────────────────────────────┘   │
│    ┌──────────────────────────────────────────────────────┐   │
│    │ □ ProTrial Subscription: Activate Pro trial          │   │
│    │   • Call subscription API with CDK code              │   │
│    └──────────────────────────────────────────────────────┘   │
│    ┌──────────────────────────────────────────────────────┐   │
│    │ □ Export to 9router: Import into AI gateway          │   │
│    │   • POST /api/oauth/kiro/import                      │   │
│    │   • Send: refreshToken + clientId + clientSecret     │   │
│    └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                     ↓
                 ✅ COMPLETE
```

**Typical Duration**: 2-5 minutes per account (depends on captcha solving speed)

---

## GUI Checkbox Options

### Auto-Register Tab Options

#### 🔲 Headless

**What it does**: Runs the browser in headless mode (no visible window).

**Technical Details**:
- Chromium launches with `--headless=new` flag
- No GUI window appears during registration
- Screenshots still captured for debugging
- Slightly faster (~5-10% performance gain)

**When to enable**:
- ✅ Batch registrations (10+ accounts)
- ✅ Server/headless environments
- ✅ Background automation
- ✅ When you don't need to watch the process

**When to disable**:
- ❌ Debugging registration issues
- ❌ First-time setup (watch flow to verify)
- ❌ AWS TES blocks (headless Chrome is heavily profiled)
- ❌ Manual captcha solving (you need to see the challenge)

**AWS TES Impact**: ⚠️ **HIGH RISK** - TES (Trust Evaluation Service) heavily profiles headless browsers. If you're getting blocked, disable this first.

---

#### 🔲 Auto Sign-In

**What it does**: Automatically launches Kiro desktop app after registration and signs in with the new account.

**Technical Details**:
- Writes tokens to `~/.aws/sso/cache/kiro-auth-token.json`
- Writes client credentials to `~/.aws/sso/cache/{clientIdHash}.json`
- Launches Kiro with `--no-sandbox` flag (required for automation)
- Kiro detects cached tokens and auto-authenticates

**When to enable**:
- ✅ You want to use the account immediately in Kiro desktop
- ✅ Testing if account works before batch registration
- ✅ Single account registration for personal use

**When to disable**:
- ❌ Batch registration (launching app 50+ times is slow)
- ❌ Accounts are for 9router export only
- ❌ Running on server without GUI (Kiro can't launch)
- ❌ You'll manually configure accounts later

**Performance Impact**: ~5-10 seconds per account (app launch time)

**Code Location**: `kiro_register.py:441-453` (skip_onboarding function)

---

#### 🔲 Skip Onboarding

**What it does**: Bypasses Kiro's welcome/tutorial screens by pre-marking onboarding as complete.

**Technical Details**:
- Modifies `%APPDATA%/Kiro/User/globalStorage/storage.json` (Windows)
- Or `~/.config/Kiro/User/globalStorage/storage.json` (Linux/Mac)
- Sets `kiroAgent.onboarding.onboardingCompleted: "true"`
- Only works if "Auto Sign-In" is enabled

**When to enable**:
- ✅ Always enable if using "Auto Sign-In"
- ✅ You've seen the onboarding before
- ✅ Batch registration + immediate use

**When to disable**:
- ❌ First-time user (you want to see the tutorial)
- ❌ "Auto Sign-In" is disabled (no effect)

**Note**: Harmless to enable - just saves ~30 seconds of clicking through welcome screens.

---

#### 🔲 ProTrial Subscription

**What it does**: Automatically activates Kiro Pro trial subscription using CDK (activation) codes.

**Technical Details**:
- Calls Kiro subscription API: `POST /api/subscription/activate`
- Requires valid CDK codes in `kiro_config.json` → `cdk_codes` array
- Each account consumes one CDK code
- Sets database field `is_pro = true`
- Pro features:
  - Longer context window
  - Priority access
  - Advanced AI capabilities
  - No daily usage limits

**When to enable**:
- ✅ You have CDK codes to use
- ✅ Accounts need Pro features
- ✅ Testing Pro functionality

**When to disable**:
- ❌ No CDK codes available (will fail)
- ❌ Free tier is sufficient for your use case
- ❌ Saving CDK codes for specific accounts

**Error Handling**:
- If subscription fails: See "Persist Without Trial" option
- Failure doesn't stop registration (account still created)

**Code Location**: `kiro_subscribe.py` (subscription logic)

---

#### 🔲 Persist Without Trial

**What it does**: Keeps the account in database even if Pro subscription activation fails.

**Technical Details**:
- Default behavior: Stop registration if Pro subscription fails
- With this enabled: Save account to database even if subscription fails
- Useful when CDK codes are limited or subscription service is down

**When to enable**:
- ✅ You want all registered accounts saved, regardless of Pro status
- ✅ CDK codes might be invalid/expired
- ✅ Subscription service is unreliable
- ✅ You'll manually upgrade accounts later

**When to disable**:
- ❌ Only want Pro accounts in database
- ❌ CDK codes are guaranteed valid
- ❌ Strict quality control (Pro-only)

**Example Scenario**: You have 10 CDK codes but register 20 accounts. With this enabled, all 20 accounts are saved (10 Pro, 10 Free). With this disabled, only the 10 Pro accounts are saved.

---

#### 🔲 Fingerprint Browser

**What it does**: Randomizes browser fingerprints to avoid AWS Trust Evaluation Service (TES) detection.

**Technical Details**:
- Randomizes per account:
  - **User Agent**: Chrome versions 130-133
  - **Viewport Size**: 1280x800 to 1920x1080
  - **Screen Resolution**: 1366x768 to 3440x1440
  - **WebGL Vendor**: Google Inc. (NVIDIA/AMD/Intel)
  - **WebGL Renderer**: Various GPU models (RTX 3060, RX 6700, etc.)
  - **Timezone**: US/Canada/UK timezones
  - **Locale**: en-US, en-GB, en-CA, en-AU
  - **Platform**: Windows 10/11
- Applies `playwright-stealth` patches to hide automation signals

**When to enable**:
- ✅ **ALWAYS RECOMMENDED** for production use
- ✅ Batch registration (reduces pattern detection)
- ✅ Avoiding TES blocks
- ✅ Using residential proxies (maximize diversity)

**When to disable**:
- ❌ Debugging (consistent fingerprint helps isolate issues)
- ❌ Single test registration
- ❌ You want to test with specific browser config

**AWS TES Impact**: 🛡️ **REDUCES RISK** - Randomized fingerprints make accounts look like different users on different machines.

**Code Location**: `kiro_register.py:61-99` (fingerprint randomization constants)

---

## Configuration Settings

### Settings Tab (Main GUI)

Before starting registration, you need to configure these settings:

---

### 📧 Mail Provider Configuration

**Location**: Settings Tab → Mail Provider Section

#### Mail Provider Selection
- **Dropdown**: Choose between `shiromail`, `yydsmail`, or `gsuite_imap`
- Each provider requires different configuration

---

#### ShiroMail Configuration

**API URL**:
- Default: `https://api.shiromail.com`
- Custom: Your self-hosted ShiroMail instance URL
- Format: `https://yourdomain.com` or `http://localhost:8080`

**API Key**:
- Required: Yes
- Where to get: ShiroMail service provider
- Format: Long alphanumeric string
- Example: `sk_1a2b3c4d5e6f7g8h9i0j`

**Domain ID**:
- Required: Yes
- Purpose: Selects which email domain to use
- Format: Integer (1, 2, 3, etc.)
- How to find: Check your ShiroMail account for available domain IDs
- Common values:
  - `1` = Primary domain
  - `2` = Secondary domain

**Example Configuration**:
```json
{
  "mail_provider": "shiromail",
  "shiromail": {
    "base_url": "https://api.shiromail.com",
    "api_key": "sk_1a2b3c4d5e6f7g8h9i0j",
    "domain_id": 1
  }
}
```

---

#### YYDSMail Configuration

**API URL**:
- Default: `https://api.yydsmail.com`
- Custom: Your self-hosted instance

**API Key**:
- Required: Yes
- Where to get: YYDSMail service provider
- Format: Similar to ShiroMail

**No Domain ID needed** - YYDSMail auto-assigns domains

---

#### GSuite IMAP (Self-Hosted)

**IMAP Host**:
- Gmail: `imap.gmail.com`
- Google Workspace: `imap.gmail.com`
- Custom: Your email server hostname

**IMAP Port**:
- Standard SSL: `993`
- Standard TLS: `143`
- Custom: Your server's port

**Username**:
- Your catch-all email address
- Example: `catchall@yourdomain.com`
- Must have IMAP access enabled

**Password**:
- Gmail: Use App Password (not your account password)
- How to generate: Google Account → Security → App Passwords
- Workspace: Admin-generated app password or regular password

**Domains File**:
- Default: `domains.txt` (in project root)
- Format: One domain per line
- Example:
  ```
  domain1.com
  domain2.net
  domain3.org
  ```
- Purpose: Rotates through your owned domains for email generation

**Example Configuration**:
```json
{
  "mail_provider": "gsuite_imap",
  "gsuite_imap": {
    "host": "imap.gmail.com",
    "port": 993,
    "user": "catchall@mydomain.com",
    "password": "app-password-here",
    "domains_file": "domains.txt"
  }
}
```

---

### 🤖 Captcha Solver Configuration

**Location**: Settings Tab → Captcha Section

#### Captcha Provider Selection
- **Dropdown**: Choose between `yescaptcha` or `multibot`
- Both solve hCaptcha challenges automatically
- Leave empty for manual solving

---

#### YesCaptcha Configuration

**API Key**:
- Required: For automatic solving
- Where to get: https://yescaptcha.com
- Format: Long alphanumeric string
- Cost: ~$0.001-0.003 per captcha
- Speed: 30-60 seconds per solve

**When to use**:
- ✅ Batch registration (100+ accounts)
- ✅ Headless automation
- ✅ Fast, reliable solving

---

#### Multibot Configuration

**API Key**:
- Required: For automatic solving
- Where to get: https://multibot.cloud
- Format: Similar to YesCaptcha
- Cost: Similar pricing
- Speed: 30-90 seconds per solve

---

#### Manual Captcha Solving (No API Key)

If no captcha API key is configured:
1. Registration pauses when hCaptcha appears
2. Browser window must be visible (disable headless)
3. You manually solve the captcha
4. Click "Continue" or wait for detection
5. Registration resumes automatically

**When to use**:
- ✅ Testing/debugging
- ✅ Single account registration
- ✅ No budget for captcha service
- ❌ Batch registration (very slow)

---

### 🌐 Proxy Configuration

**Location**: Settings Tab → Proxy Section

**Proxy URL**:
- Format: `http://username:password@host:port`
- Example: `http://user123:pass456@proxy.example.com:8080`
- Required for: Avoiding AWS TES blocks at scale

**Residential Proxy Recommended**:
- ✅ Residential IPs (home/mobile networks)
- ❌ Datacenter IPs (often blocked by AWS)

**When to use**:
- ✅ Batch registration (10+ accounts/day)
- ✅ Getting TES blocks
- ✅ Previous accounts banned
- ❌ Single test registration

**Cost**: ~$5-15 per GB (residential proxies)

**Providers**:
- Bright Data
- Oxylabs
- Smartproxy
- IPRoyal

**Leave empty** if registering 1-3 accounts/day from your home IP.

---

### 🔗 9router Integration

**Location**: Settings Tab → 9router Section

**Enable Auto-Export**:
- Checkbox: Enable/disable automatic export to 9router
- When enabled: Every successfully registered account is automatically imported into 9router

**9router API URL**:
- Local: `http://localhost:20128`
- Remote: `https://xapi.fastev.my.id` (or your server)
- LAN: `http://192.168.1.100:20128`
- Format: Full URL with protocol and port

**Verify SSL**:
- Enabled: Verify SSL certificates (production)
- Disabled: Skip verification (self-signed certs, development only)

**What gets exported**:
- `refreshToken` - AWS Builder ID refresh token
- `clientId` - OAuth client identifier
- `clientSecret` - OAuth client credential

**Example Configuration**:
```json
{
  "enable_9router_export": true,
  "router9_url": "https://xapi.fastev.my.id",
  "router9_verify_ssl": true
}
```

---

## Batch Registration Behavior

### How Looping Works

The registration system is **not** a continuous infinite loop. It's a configurable batch processor:

**Batch Count Field**:
- Location: Auto-Register Tab → "Batch Count"
- Purpose: Specify how many accounts to create
- Range: 1 to 1000+ (limited by your resources)
- Default: 1

**Concurrency Field**:
- Location: Auto-Register Tab → "Concurrency"
- Purpose: How many registrations to run in parallel
- Range: 1 to 10
- Recommended: 1-3 (avoid overwhelming services)
- Example: Concurrency 3 = 3 accounts registering simultaneously

**Registration Flow**:
1. Click "Start" button
2. System creates N concurrent workers (based on Concurrency setting)
3. Each worker registers one account, then starts the next
4. Process continues until Batch Count is reached
5. System stops automatically when complete
6. Shows final statistics: X succeeded, Y failed

**Example**:
- Batch Count: 50
- Concurrency: 5
- Result: 5 accounts register simultaneously, then next 5, etc. Stops at 50 total.

**Stopping Early**:
- Click "Stop" button to halt registration mid-batch
- Currently running registrations complete
- Partial progress is saved (accounts already created remain in database)

**Progress Tracking**:
- GUI shows: "15/50 completed" live counter
- Log window shows: Per-account status
- Database: All successful accounts saved immediately

**Use Cases**:
- Single test: Batch Count = 1
- Daily registration: Batch Count = 3-5 (without proxy)
- Bulk creation: Batch Count = 50-100 (with proxy + captcha API)
- Production farming: Batch Count = 500+ (residential proxy required)

**NOT an infinite loop** - it's a "register N accounts and stop" system.

---

## Step-by-Step Process

### First-Time Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   python -m playwright install chromium
   ```

2. **Configure Mail Provider**:
   - Open GUI → Settings Tab
   - Select mail provider (ShiroMail recommended for beginners)
   - Enter API key and domain ID
   - Click "Test" to verify connection

3. **Configure Captcha Solver** (Optional but Recommended):
   - Settings Tab → Captcha section
   - Select provider (YesCaptcha or Multibot)
   - Enter API key
   - Test with a sample captcha

4. **Configure Proxy** (Optional):
   - Settings Tab → Proxy section
   - Enter residential proxy URL
   - Format: `http://user:pass@host:port`

5. **Configure 9router** (Optional):
   - Settings Tab → 9router section
   - Enable auto-export checkbox
   - Enter 9router API URL
   - Set SSL verification preference

6. **Save Configuration**:
   - Click "Save Settings"
   - Verify `kiro_config.json` created in project root

---

### Registering Your First Account

1. **Go to Auto-Register Tab**
2. **Set Options**:
   - ☐ Headless (disable for first test - you want to watch)
   - ☑ Auto Sign-In (enable to test account works)
   - ☑ Skip Onboarding (enable to save time)
   - ☐ ProTrial Subscription (enable only if you have CDK codes)
   - ☑ Persist Without Trial (enable to keep account if Pro fails)
   - ☑ Fingerprint Browser (always recommended)

3. **Set Batch Settings**:
   - Batch Count: `1`
   - Concurrency: `1`

4. **Click "Start"**
5. **Watch the Process**:
   - Browser opens (if not headless)
   - Log shows each step
   - hCaptcha appears (solve manually if no API key)
   - OTP arrives and is auto-filled
   - Tokens extracted and saved

6. **Check Results**:
   - Go to Accounts Tab
   - See your new account listed
   - Check email, status, created_at timestamp
   - If Auto Sign-In enabled: Kiro app should open and be logged in

7. **Verify 9router** (if enabled):
   - Open 9router dashboard
   - Check if account appears in pool
   - Status should show "Active"

**Expected Duration**: 2-5 minutes per account (depends on captcha solving)

---
## Troubleshooting

### Common Issues and Solutions

#### ❌ "Email provider unreachable" or "401 Unauthorized"

**Cause**: Incorrect mail provider API key or URL

**Solution**:
1. Go to Settings Tab → Mail Provider
2. Verify API key is correct (no extra spaces)
3. Test connection with "Test" button
4. Check API URL format (must include `https://`)
5. Verify domain ID exists for your account

---

#### ❌ "hCaptcha timeout" or "Manual captcha required"

**Cause**: No captcha API key configured or service unavailable

**Solution**:
1. **Automatic solving**: 
   - Settings Tab → Captcha section
   - Enter YesCaptcha or Multibot API key
   - Verify API key has credits
2. **Manual solving**:
   - Disable "Headless" checkbox
   - Browser will pause at captcha
   - Solve manually and wait for auto-resume

---

#### ❌ "OTP code not received" or "Timeout waiting for email"

**Cause**: Mail provider slow, AWS didn't send email, or TES block

**Solutions**:
1. **Check mail provider**:
   - Go to mail provider dashboard
   - Verify emails are arriving
   - Check if OTP email is in spam/trash
2. **Increase timeout**:
   - Default: 5 minutes
   - Edit `kiro_register.py:6` to increase timeout
3. **Try different mail provider**:
   - ShiroMail vs YYDSMail have different delivery speeds
   - Self-hosted IMAP with real domains is most reliable
4. **Check for TES block**:
   - See next section

---

#### ❌ "INVALID_OTP" on every submit

**Cause**: AWS Trust Evaluation Service (TES) consumed the OTP server-side during detection

**What Happened**:
1. OTP was delivered correctly
2. TES flagged your session as suspicious
3. AWS silently validated and consumed the OTP during profiling
4. When you submit, it's already been used
5. Retry gets another "INVALID_OTP" (second OTP consumed too)

**Solution**:
1. **Enable residential proxy** (required):
   - Settings Tab → Proxy section
   - Use residential IP, not datacenter
2. **Disable headless mode**:
   - Uncheck "Headless" checkbox
   - TES heavily profiles headless Chrome
3. **Slow down**:
   - Reduce concurrency to 1
   - Wait 30+ minutes between attempts from same IP
4. **Use real domains**:
   - Switch from temp mail to self-hosted IMAP
   - Real domains like `@yourdomain.com` avoid TES profiling

**Prevention**:
- Always use residential proxy for >3 accounts/day
- Keep headless disabled during TES blocks
- Randomize fingerprints (keep checkbox enabled)

---

#### ❌ "create-identity → 400 BLOCKED by TES"

**Cause**: AWS TES completely blocked your session before OTP stage

**This is more severe than INVALID_OTP** - TES blocked you at identity creation, not OTP validation.

**Solution**:
1. **Mandatory residential proxy**
2. **Wait 24 hours** before retrying from same IP
3. **Switch to real domains** (self-hosted IMAP)
4. **Reduce velocity**:
   - Max 1-3 accounts/day without proxy
   - Max 10-20 accounts/day with residential proxy
5. **Review IP_MITIGATION_GUIDE.md** for detailed strategies

---

#### ❌ "Token refresh failed: Bad credentials" (9router)

**Cause**: Missing `clientId` or `clientSecret` in export payload

**Solution**:
This is now fixed in the latest version of `router9_export.py`. If you're still getting this error:

1. **Verify database has credentials**:
   ```bash
   python3 -c "
   import sqlite3
   conn = sqlite3.connect('kiro_accounts.db')
   cursor = conn.cursor()
   cursor.execute('SELECT email, clientId, clientSecret FROM accounts LIMIT 1')
   row = cursor.fetchone()
   if row and row[1] and row[2]:
       print(f'✅ Credentials present: {row[0]}')
   else:
       print('❌ Missing credentials - account needs re-registration')
   "
   ```

2. **Re-register accounts** created before the fix (old accounts don't have clientId/clientSecret stored)

3. **Manual export with full credentials**:
   ```bash
   curl -X POST https://xapi.fastev.my.id/api/oauth/kiro/import \
     -H "Content-Type: application/json" \
     -d '{
       "refreshToken": "YOUR_TOKEN",
       "clientId": "YOUR_CLIENT_ID",
       "clientSecret": "YOUR_CLIENT_SECRET"
     }'
   ```

---

#### ❌ "Kiro app won't launch" or "Auto Sign-In failed"

**Cause**: Kiro not installed or path incorrect

**Solution**:
1. **Windows**: Verify Kiro installed at `C:\Users\<username>\AppData\Local\Programs\Kiro\Kiro.exe`
2. **Linux**: Check `/usr/bin/kiro` or `~/.local/bin/kiro`
3. **Mac**: Check `/Applications/Kiro.app`
4. **Edit path** in `main.py:~1700` if custom installation location
5. **Disable Auto Sign-In** if running on headless server (no GUI)

---

#### ❌ "Pro subscription failed" but account still saved

**Expected behavior** if "Persist Without Trial" is enabled!

**Check status**:
```python
import sqlite3
conn = sqlite3.connect('kiro_accounts.db')
cursor = conn.cursor()
cursor.execute('SELECT email, is_pro FROM accounts')
for row in cursor.fetchall():
    status = "Pro ✅" if row[1] else "Free ⚠️"
    print(f'{row[0]}: {status}')
```

**Manually upgrade later**:
1. Use `kiro_subscribe.py` script
2. Or use Kiro desktop app's subscription UI

---

#### ❌ Database locked or permission denied

**Cause**: Multiple processes accessing `kiro_accounts.db` simultaneously

**Solution**:
1. Stop all running registrations
2. Close Kiro GUI
3. Check for stuck processes: `ps aux | grep python | grep kiro`
4. Kill stuck processes: `kill <pid>`
5. Restart registration

---

#### ❌ "playwright._impl._api_types.Error: Executable doesn't exist"

**Cause**: Playwright browser runtime not installed

**Solution**:
```bash
python -m playwright install chromium
```

If that fails:
```bash
playwright install chromium
```

---

## 9router Integration

### Automatic Export After Registration

**How it works**:
1. Account registers successfully
2. Tokens extracted (refreshToken, clientId, clientSecret)
3. If "Enable 9router Export" is checked in Settings:
   - System calls 9router API: `POST /api/oauth/kiro/import`
   - Sends all three credentials
   - 9router validates token with AWS
   - Account added to 9router pool
4. Status shown in log: "✓ user@example.com successfully exported to 9router"

**Failure handling**:
- Export failure doesn't stop registration
- Account still saved in local database
- You can manually export later

---

### Manual Bulk Export

**Export all accounts from database**:

```bash
python3 << 'EOF'
from router9_export import export_from_database

result = export_from_database(
    db_path="kiro_accounts.db",
    base_url="https://xapi.fastev.my.id",
    log=print
)

print(f"\n✅ Exported: {result['exported']}")
print(f"❌ Failed: {result['failed']}")
## Troubleshooting

### Common Issues and Solutions

#### ❌ "Email provider unreachable" or "401 Unauthorized"

**Cause**: Incorrect mail provider API key or URL

**Solution**:
1. Go to Settings Tab → Mail Provider
2. Verify API key is correct (no extra spaces)
3. Test connection with "Test" button
4. Check API URL format (must include `https://`)
5. Verify domain ID exists for your account

---

#### ❌ "hCaptcha timeout" or "Manual captcha required"

**Cause**: No captcha API key configured or service unavailable

**Solution**:
1. **Automatic solving**: 
   - Settings Tab → Captcha section
   - Enter YesCaptcha or Multibot API key
   - Verify API key has credits
2. **Manual solving**:
   - Disable "Headless" checkbox
   - Browser will pause at captcha
   - Solve manually and wait for auto-resume

---

#### ❌ "OTP code not received" or "Timeout waiting for email"

**Cause**: Mail provider slow, AWS didn't send email, or TES block

**Solutions**:
1. **Check mail provider**:
   - Go to mail provider dashboard
   - Verify emails are arriving
   - Check if OTP email is in spam/trash
2. **Increase timeout**:
   - Default: 5 minutes
   - Edit `kiro_register.py` timeout parameter
3. **Try different mail provider**:
   - ShiroMail vs YYDSMail have different delivery speeds
   - Self-hosted IMAP with real domains is most reliable
4. **Check for TES block** (see next section)

---

#### ❌ "INVALID_OTP" on every submit

**Cause**: AWS Trust Evaluation Service (TES) consumed the OTP server-side during detection

**What Happened**:
1. OTP was delivered correctly
2. TES flagged your session as suspicious
3. AWS silently validated and consumed the OTP during profiling
4. When you submit, it's already been used
5. Retry gets another "INVALID_OTP" (second OTP consumed too)

**Solution**:
1. **Enable residential proxy** (required):
   - Settings Tab → Proxy section
   - Use residential IP, not datacenter
2. **Disable headless mode**:
   - Uncheck "Headless" checkbox
   - TES heavily profiles headless Chrome
3. **Slow down**:
   - Reduce concurrency to 1
   - Wait 30+ minutes between attempts from same IP
4. **Use real domains**:
   - Switch from temp mail to self-hosted IMAP
   - Real domains like `@yourdomain.com` avoid TES profiling

**Prevention**:
- Always use residential proxy for >3 accounts/day
- Keep headless disabled during TES blocks
- Randomize fingerprints (keep checkbox enabled)

---

#### ❌ "create-identity → 400 BLOCKED by TES"

**Cause**: AWS TES completely blocked your session before OTP stage

**This is more severe than INVALID_OTP** - TES blocked you at identity creation, not OTP validation.

**Solution**:
1. **Mandatory residential proxy**
2. **Wait 24 hours** before retrying from same IP
3. **Switch to real domains** (self-hosted IMAP)
4. **Reduce velocity**:
   - Max 1-3 accounts/day without proxy
   - Max 10-20 accounts/day with residential proxy
5. **Review IP_MITIGATION_GUIDE.md** for detailed strategies

---

#### ❌ "Token refresh failed: Bad credentials" (9router)

**Cause**: Missing `clientId` or `clientSecret` in export payload

**Solution**:
This is now fixed in the latest version of `router9_export.py`. If you're still getting this error:

1. **Verify database has credentials** - Check if clientId and clientSecret are present
2. **Re-register accounts** created before the fix (old accounts don't have clientId/clientSecret stored)
3. **Manual export with full credentials** - Use the curl examples provided earlier

---

#### ❌ "Kiro app won't launch" or "Auto Sign-In failed"

**Cause**: Kiro not installed or path incorrect

**Solution**:
1. **Windows**: Verify Kiro installed at `C:\Users\<username>\AppData\Local\Programs\Kiro\Kiro.exe`
2. **Linux**: Check `/usr/bin/kiro` or `~/.local/bin/kiro`
3. **Mac**: Check `/Applications/Kiro.app`
4. **Edit path** in `main.py` if custom installation location
5. **Disable Auto Sign-In** if running on headless server (no GUI)

---

#### ❌ "Pro subscription failed" but account still saved

**Expected behavior** if "Persist Without Trial" is enabled!

**Manually upgrade later**:
1. Use `kiro_subscribe.py` script
2. Or use Kiro desktop app's subscription UI

---

#### ❌ Database locked or permission denied

**Cause**: Multiple processes accessing `kiro_accounts.db` simultaneously

**Solution**:
1. Stop all running registrations
2. Close Kiro GUI
3. Check for stuck processes: `ps aux | grep python | grep kiro`
4. Kill stuck processes: `kill <pid>`
5. Restart registration

---

#### ❌ "playwright._impl._api_types.Error: Executable doesn't exist"

**Cause**: Playwright browser runtime not installed

**Solution**:
```bash
python -m playwright install chromium
```

---

## 9router Integration

### Automatic Export After Registration

**How it works**:
1. Account registers successfully
2. Tokens extracted (refreshToken, clientId, clientSecret)
3. If "Enable 9router Export" is checked in Settings:
   - System calls 9router API: `POST /api/oauth/kiro/import`
   - Sends all three credentials
   - 9router validates token with AWS
   - Account added to 9router pool
4. Status shown in log: "✓ user@example.com successfully exported to 9router"

**Failure handling**:
- Export failure doesn't stop registration
- Account still saved in local database
- You can manually export later

---

### Manual Bulk Export

Export all accounts from database to 9router using the `router9_export.py` module.

---

### Verify Import in 9router

1. Open 9router dashboard
2. Check account pool
3. Verify account appears with:
   - Email address
   - Provider: "BuilderId"
   - Status: "Active"
4. Test account by making a request through 9router

---

## Best Practices

### Production Use

1. **Always use residential proxy** for >5 accounts/day
2. **Enable fingerprint randomization** (always)
3. **Use captcha API** (YesCaptcha/Multibot) for batch registration
4. **Disable headless** if getting TES blocks
5. **Real domains > temp mail** for reliability
6. **Concurrency ≤ 3** to avoid overwhelming services
7. **Monitor ban rate**: If >10% banned, slow down or improve setup

---

### Cost Estimation

**Per Account**:
- Captcha solving: $0.001-0.003
- Temp mail: $0.001-0.01 (or free with self-hosted IMAP)
- Residential proxy: ~$0.01-0.05 (depends on provider and data usage)
- **Total**: ~$0.01-0.08 per account

**Batch Registration (100 accounts)**:
- Captcha: ~$0.20
- Temp mail: ~$1.00 (or $0 with IMAP)
- Proxy: ~$3.00
- **Total**: ~$4-5 for 100 accounts

---

## Summary

This tool automates AWS Builder ID registration with:
- ✅ Pluggable temp mail providers
- ✅ Automatic hCaptcha solving
- ✅ Anti-detection fingerprinting
- ✅ Token extraction and secure storage
- ✅ Optional Pro subscription activation
- ✅ Automatic 9router export
- ✅ Batch registration with configurable concurrency

**Key Points**:
1. **Not an infinite loop** - Specify batch count, system stops when complete
2. **Configuration required** - Set up mail provider, captcha API, proxy before use
3. **TES awareness** - Use residential proxy and real domains at scale
4. **Client credentials matter** - refreshToken alone is not enough for 9router
5. **Monitoring important** - Track ban rate and adjust strategy

**Related Documentation**:
- `CLAUDE.md` - Developer guide and architecture
- `IP_MITIGATION_GUIDE.md` - Detailed TES avoidance strategies
- `README.md` - Project overview and quick start

---

**Last Updated**: 2026-07-09

For issues or questions, refer to the project's GitHub repository or CLAUDE.md troubleshooting section.
