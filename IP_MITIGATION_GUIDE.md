# IP Mitigation Guide - Registering Without a Proxy

This guide provides strategies to minimize IP-based rate limiting and detection when registering multiple Kiro accounts **without using a residential proxy**.

## ⚠️ Important Context

AWS uses **Trust Evaluation Service (TES)** to detect automated registrations. TES monitors:
- Registration velocity from the same IP
- Browser fingerprint consistency
- Behavioral patterns (typing speed, mouse movements)
- Email provider reputation (temp mail services are flagged)
- Account usage patterns after registration

**Without a proxy, you are limited by:**
- Single IP address = easier to track velocity
- ISP reputation (residential IPs are better than datacenter)
- Cumulative risk (each registration increases scrutiny)

---

## 🎯 Recommended Strategy: Slow & Steady

**Golden Rule:** Quality over quantity. 1-3 successful accounts per day is better than 20 banned accounts.

### Daily Limits (Without Proxy)
- **Conservative:** 1-2 accounts per day
- **Moderate:** 3-5 accounts per day (higher risk)
- **Aggressive:** 6+ per day (very high ban rate)

### Timing Between Registrations
```
First attempt:  Immediate
After success:  Wait 4-8 hours
After failure:  Wait 2-4 hours
After ban:      Wait 24 hours
```

---

## 📋 Configuration Changes for IP Mitigation

### 1. Increase Registration Delays

Edit `kiro_register.py` to add longer delays between steps:

```python
# Current default delays (too fast without proxy)
await asyncio.sleep(2)  # Standard delay

# Recommended delays without proxy
await asyncio.sleep(random.uniform(3, 7))  # 3-7 seconds
await asyncio.sleep(random.uniform(5, 10)) # 5-10 seconds for critical steps
```

**Where to add delays:**
- After page navigation (line ~810)
- After email submission (line ~900)
- Before OTP entry (line ~1180)
- After password setup (line ~1310)

### 2. Reduce Retry Aggressiveness

In `main.py`, change the retry logic:

```python
# Current: 5 retries (line 1562)
MAX_RETRY = 5

# Recommended: 2-3 retries without proxy
MAX_RETRY = 2  # Stop sooner to avoid velocity detection
```

### 3. Add Exponential Backoff

Replace the fixed retry delay with exponential backoff:

```python
# Current retry delay (line 1657-1659)
if attempt > 1:
    wait = random.randint(10, 30)
    self._reg_queue.put((f"Wait {wait}s before starting #{attempt}...", "info"))
    time.sleep(wait)

# Recommended: Exponential backoff
if attempt > 1:
    base_wait = 60 * (2 ** (attempt - 1))  # 60s, 120s, 240s...
    jitter = random.randint(-15, 15)
    wait = base_wait + jitter
    self._reg_queue.put((f"Exponential backoff: {wait}s before attempt #{attempt}...", "info"))
    time.sleep(wait)
```

---

## 🔧 Enhanced Fingerprint Randomization

The existing fingerprint randomization is good, but here are ways to enhance it:

### 1. Rotate Fingerprints More Aggressively

Edit `kiro_register.py` line ~146 to add more variety:

```python
def _random_fingerprint_config():
    """Enhanced fingerprint config for IP mitigation."""
    screen = secrets.choice(_SCREEN_RESOLUTIONS)
    viewport = secrets.choice(_VIEWPORT_SIZES)
    
    # Add more randomization
    return {
        "viewport": {"width": viewport[0], "height": viewport[1]},
        "screen": {"width": screen[0], "height": screen[1]},
        "locale": secrets.choice(_LOCALES),
        "timezone": secrets.choice(_TIMEZONES),
        "user_agent": _random_ua(),
        "webgl_vendor": secrets.choice(_WEBGL_VENDORS),
        "webgl_renderer": secrets.choice(_WEBGL_RENDERERS),
        "hardware_concurrency": secrets.choice([4, 6, 8, 12, 16]),
        "device_memory": secrets.choice([4, 8, 16, 32]),
        "color_depth": 24,
        "pixel_ratio": secrets.choice([1.0, 1.25, 1.5, 2.0]),
        "max_touch_points": 0,
        "platform": "Win32",
        "canvas_noise": secrets.token_hex(4),
        
        # NEW: Add these for better differentiation
        "languages": [secrets.choice(_LOCALES)],
        "do_not_track": secrets.choice([None, "1"]),
        "connection_type": secrets.choice(["4g", "wifi", "ethernet"]),
    }
```

### 2. Disable Headless Mode

Headless browsers are easier to detect. In the GUI:
- **Uncheck "Headless"** checkbox
- TES detection rate drops significantly with visible browser

### 3. Use Self-Hosted IMAP Instead of Temp Mail

Temp mail services (ShiroMail, YYDSMail) are heavily flagged by TES.

**Best approach:**
1. Buy 2-3 cheap domains ($1-2 each)
2. Set up catch-all forwarding to your Gmail
3. Use `gsuite_imap` provider in the GUI

```
Settings in GUI:
Mail Provider: Self-hosted catch-all IMAP
IMAP Host: imap.gmail.com
IMAP Port: 993
IMAP User: your-email@gmail.com
IMAP Pass: [app password]
Domains: Add your domains to domains.txt
```

**Why this helps:**
- Real domains have better reputation than temp mail
- TES sees different @domain.com for each registration
- Reduces "same email pattern" detection

---

## 🧠 Behavioral Improvements

### 1. Longer "Warm-Up" Time on Pages

Before filling forms, simulate reading the page:

```python
# Add to kiro_register.py after page load (line ~920)
async def _simulate_page_reading(page, duration_range=(8, 15)):
    """Simulate human reading/browsing the page."""
    import random as _random
    
    duration = _random.uniform(*duration_range)
    vp = page.viewport_size
    
    # Random mouse movements while "reading"
    for _ in range(_random.randint(3, 6)):
        await page.mouse.move(
            _random.randint(100, vp["width"] - 100),
            _random.randint(100, vp["height"] - 100),
            steps=_random.randint(15, 30)
        )
        await asyncio.sleep(_random.uniform(1.5, 3.0))
    
    # Random scrolling
    for _ in range(_random.randint(2, 4)):
        await page.mouse.wheel(0, _random.randint(80, 200))
        await asyncio.sleep(_random.uniform(0.8, 1.5))
```

Call this after landing on the registration page (line ~908).

### 2. Slower, More Human Typing

Current typing is already good, but can be enhanced:

```python
# In _human_type function (line ~327), increase delays
async def _human_type(page, locator, text, min_delay=80, max_delay=250):  # Slower
    """More realistic typing with occasional pauses."""
    await locator.click()
    await asyncio.sleep(_random.uniform(0.3, 0.8))
    await locator.fill("")
    
    for i, ch in enumerate(text):
        await page.keyboard.type(ch, delay=0)
        
        # Base delay
        delay = _random.uniform(min_delay, max_delay) / 1000
        
        # Occasional long pauses (thinking, looking away)
        if i > 0 and _random.random() < 0.12:  # 12% chance
            delay += _random.uniform(0.4, 1.2)
        
        # Slower after punctuation or space
        if ch in ".,;: ":
            delay += _random.uniform(0.1, 0.3)
        
        await asyncio.sleep(delay)
    
    await asyncio.sleep(_random.uniform(0.5, 1.2))
```

### 3. Add "Mistakes" and Corrections

Real humans make typos:

```python
async def _human_type_with_mistakes(page, locator, text, mistake_rate=0.08):
    """Type with occasional typos and corrections."""
    await locator.click()
    await asyncio.sleep(_random.uniform(0.3, 0.8))
    await locator.fill("")
    
    for i, ch in enumerate(text):
        # Occasionally type wrong char then backspace
        if _random.random() < mistake_rate:
            wrong_char = _random.choice("asdfghjkl")
            await page.keyboard.type(wrong_char, delay=0)
            await asyncio.sleep(_random.uniform(0.1, 0.2))
            await page.keyboard.press("Backspace")
            await asyncio.sleep(_random.uniform(0.2, 0.4))
        
        await page.keyboard.type(ch, delay=0)
        delay = _random.uniform(80, 250) / 1000
        await asyncio.sleep(delay)
    
    await asyncio.sleep(_random.uniform(0.5, 1.2))
```

---

## 📊 Account Health Monitoring

### 1. Pre-Registration Health Check

Before starting a new registration, check your existing accounts:

```python
# Run this command to check account health
python main.py
# In GUI: Account Manager tab → Health Check (select all accounts)
```

**Red flags to stop registering:**
- Multiple recent bans (>50% in last 24h)
- "TEMPORARILY_SUSPENDED" errors
- Consistent "403 Forbidden" on token refresh

**Safe to continue:**
- All accounts healthy and active
- No bans in last 48 hours
- Tokens refreshing successfully

### 2. Adaptive Registration Rate

Implement dynamic rate limiting based on success rate:

```python
# Add to main.py registration logic
class RegistrationRateLimiter:
    def __init__(self):
        self.last_24h_attempts = []
        self.last_24h_bans = []
    
    def should_allow_registration(self):
        now = time.time()
        cutoff = now - (24 * 3600)
        
        # Clean old entries
        self.last_24h_attempts = [t for t in self.last_24h_attempts if t > cutoff]
        self.last_24h_bans = [t for t in self.last_24h_bans if t > cutoff]
        
        attempts_24h = len(self.last_24h_attempts)
        bans_24h = len(self.last_24h_bans)
        
        # Stop if >3 attempts in 24h
        if attempts_24h >= 3:
            return False, "Daily limit reached (3 attempts per 24h)"
        
        # Stop if ban rate >30%
        if attempts_24h > 0 and (bans_24h / attempts_24h) > 0.3:
            return False, f"High ban rate detected ({bans_24h}/{attempts_24h})"
        
        return True, "OK"
    
    def record_attempt(self, banned=False):
        now = time.time()
        self.last_24h_attempts.append(now)
        if banned:
            self.last_24h_bans.append(now)
```

---

## 🕐 Registration Scheduling Strategy

### Time-of-Day Considerations

TES monitoring varies by time:

**Best times (US Eastern):**
- **6am-9am ET:** Lower traffic, fewer suspicious patterns
- **2pm-5pm ET:** Business hours, looks more natural
- **Avoid:** Late night (11pm-4am) = suspicious timing

**Day-of-Week:**
- **Best:** Tuesday-Thursday (normal workday pattern)
- **Avoid:** Weekends + Monday mornings

### Implementation

Add a time-based scheduler:

```python
def is_good_registration_time():
    """Check if current time is optimal for registration."""
    import pytz
    from datetime import datetime
    
    et = pytz.timezone('US/Eastern')
    now_et = datetime.now(et)
    hour = now_et.hour
    weekday = now_et.weekday()  # 0=Monday, 6=Sunday
    
    # Weekends: not optimal
    if weekday >= 5:
        return False, "Weekend - wait until Tuesday"
    
    # Monday morning rush: not optimal
    if weekday == 0 and hour < 10:
        return False, "Monday morning - wait until afternoon"
    
    # Good hours: 6-9am or 2-5pm ET
    if (6 <= hour < 9) or (14 <= hour < 17):
        return True, "Optimal time"
    
    # Late night: suspicious
    if hour < 6 or hour >= 23:
        return False, f"Late night ({hour}:00 ET) - wait until 6am-9am"
    
    return True, "Acceptable time"
```

---

## 🎛️ Recommended Configuration (kiro_config.json)

```json
{
  "mail_provider": "gsuite_imap",
  "imap_server": "imap.gmail.com",
  "imap_port": 993,
  "imap_user": "your-email@gmail.com",
  "imap_pass": "your-app-password",
  "imap_domains_file": "domains.txt",
  
  "yescaptcha_key": "your-key-here",
  "captcha_provider": "yescaptcha",
  
  "cdk_code": "",
  "proxy_url": "",
  
  "enable_9router_export": true,
  "router9_url": "http://localhost:20128",
  
  "_registration_limits": {
    "max_per_day": 2,
    "min_delay_between_hours": 4,
    "exponential_backoff": true,
    "stop_on_ban_rate": 0.3
  }
}
```

---

## 📈 Success Metrics

### Track These Numbers

Keep a log of your registration attempts:

```
Date       | Attempts | Success | Banned | Ban Rate | Notes
-----------|----------|---------|--------|----------|------------------
2026-07-09 | 2        | 2       | 0      | 0%       | Used slow timing
2026-07-10 | 3        | 2       | 1      | 33%      | One TES block
2026-07-11 | 0        | 0       | 0      | N/A      | Cool-down day
2026-07-12 | 1        | 1       | 0      | 0%       | Back to normal
```

**Green flags (keep going):**
- Ban rate < 20%
- Successful token refresh on all accounts
- Pro trial subscriptions working

**Red flags (pause for 48h):**
- Ban rate > 30%
- Multiple "TEMPORARILY_SUSPENDED" errors
- OTP codes timing out consistently
- "INVALID_OTP" on first submit (TES consumed it)

---

## 🚨 Emergency Procedures

### If You Hit a Ban Wave

1. **Stop immediately** - Don't retry
2. **Wait 48 hours minimum**
3. **Check existing accounts** - If they're banned too, wait 7 days
4. **Switch domains** - Use different domains from domains.txt
5. **Reduce velocity** - Cut daily target in half

### If TES Blocks You Repeatedly

TES showing these patterns:
- OTP never validates (stuck on verification page)
- `create-identity` returns 400 with errorCode=BLOCKED
- Browser redirects loop between signin.aws and profile.aws

**Solutions:**
1. **Enable visible browser** (uncheck "Headless")
2. **Wait 24 hours** before next attempt
3. **Use real domain emails** (not temp mail)
4. **Slow down typing speed** (increase delays)
5. **Consider getting a residential proxy** (long-term solution)

---

## 💡 Advanced Strategies

### 1. Multi-Day Registration Campaign

Instead of bulk registration:

```
Day 1: Register 1 account, test it, verify it works
Day 2: Register 1 account, test it
Day 3: Wait (no registration)
Day 4: Register 2 accounts (since previous ones succeeded)
Day 5: Wait
Day 6: Register 1-2 accounts
```

### 2. "Aging" Accounts Before Use

Don't use accounts immediately after registration:

```
1. Register account
2. Wait 12-24 hours
3. Sign in manually once (not via automation)
4. Make 1-2 API calls
5. Wait another 24 hours
6. Now safe for heavy use in 9router
```

This builds account "legitimacy" and reduces ban risk.

### 3. Use RoxyBrowser (Fingerprint Browser)

If you have budget for it:

1. Sign up for RoxyBrowser: https://roxybrowser.com
2. Get API key
3. In GUI: Check "Fingerprint browser" checkbox
4. Add Roxy API key in settings

**Why it helps:**
- Uses real browser fingerprints from actual devices
- Better than randomized fingerprints
- Costs ~$20/month but significantly reduces bans

---

## 📝 Quick Reference Checklist

Before starting batch registration **without proxy:**

- [ ] Max 2-3 accounts per day
- [ ] Wait 4-8 hours between attempts
- [ ] Use self-hosted IMAP (not temp mail)
- [ ] Headless mode: **OFF**
- [ ] Typing delays: 80-250ms
- [ ] Exponential backoff: **ON**
- [ ] Recent ban rate: < 20%
- [ ] Time of day: 6-9am or 2-5pm ET
- [ ] Day of week: Tuesday-Thursday
- [ ] Last attempt: > 4 hours ago

---

## 🔗 Related Files

- `kiro_register.py` - Main registration logic
- `main.py` - GUI and orchestration
- `kiro_config.json` - Configuration file
- `router9_export.py` - 9router integration (NEW)
- `domains.txt` - Domain list for self-hosted IMAP

---

## 🎓 Summary

**Key Takeaways:**

1. **Without a proxy, velocity is your enemy** - Slow down, spread registrations across days
2. **TES watches behavioral patterns** - More human-like = fewer bans
3. **Real domains > Temp mail** - Invest $2 in a domain for better success rate
4. **Visible browser > Headless** - Easier to detect automation in headless mode
5. **Track your metrics** - Adjust strategy based on ban rate
6. **Quality over quantity** - 10 working accounts > 50 banned accounts

**Realistic expectations without proxy:**
- 1-2 accounts per day consistently = **sustainable**
- 5-10 accounts per day = **high ban rate, not sustainable**
- 20+ accounts per day = **near 100% ban rate**

If you need higher volume, investing in a residential proxy ($10-30/month) is the only sustainable solution.

---

## 📞 9router Integration Usage

After completing this guide's strategies, your accounts will auto-export to 9router if enabled.

**Enable 9router export:**
1. Install 9router: `npm install -g 9router`
2. Run 9router: `9router` (opens at http://localhost:20128)
3. Edit `kiro_config.json`:
   ```json
   {
     "enable_9router_export": true,
     "router9_url": "http://localhost:20128"
   }
   ```
4. Register accounts - they'll auto-export after success

**Manual bulk export:**
```bash
# Export all accounts from database to 9router
python router9_export.py
```

**Check 9router dashboard:**
- Open: http://localhost:20128/dashboard
- Navigate to: Providers → Kiro AI
- Your accounts will appear automatically

---

**Last Updated:** 2026-07-09
