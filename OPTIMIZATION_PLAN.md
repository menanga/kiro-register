# Kiro Registration Optimization Plan

## Critical Fixes

### 1. Fix Verification URL Format (Line 1624-1634)
**Problem:** 9router API returns malformed URL, missing `#/device?user_code=` fragment.

**Fix:**
```python
# Line 1624-1634 in kiro_register.py
verify_url_raw = dev["verification_uri_complete"]
user_code = dev.get("user_code", "")

# Reconstruct correct URL format if malformed
if user_code and "#/device?user_code=" not in verify_url_raw:
    verify_url = f"https://view.awsapps.com/start/#/device?user_code={user_code}"
    log(f"Corrected malformed verification URL → {verify_url}", "warn")
else:
    verify_url = verify_url_raw
```

---

## Speed Optimizations

### 2. Reduce Human Typing Delays (10x speedup on forms)
**Current:** `_human_type()` uses 35-120ms per char = 1-2s per email
**Grok:** Direct `fill()` = instant

**Change:**
```python
# kiro_register.py:327-339 — replace _human_type with fast fill
async def _fast_type(page, locator, text):
    """Fast form fill with minimal delay."""
    await locator.click()
    await asyncio.sleep(0.1)
    await locator.fill(text)
    await asyncio.sleep(0.2)
```

**Apply to:**
- Email input (line 887, 1025, 1742)
- Name input (line 1051, 1833)
- Password input (line 2103, 2141)

### 3. Reduce Sleep Delays (30-40s saved per account)
**Current waits:**
- Line 811: 3s after page load
- Line 837: 3s after button click
- Line 871: 3s after navigation
- Line 1009: 2s warmup
- Line 1099: 3s before OTP
- Line 1181: 2-4s human delay
- Line 2060: 3s after OTP submit
- Line 2176: 3s after password
- Line 2180: 3s before device auth

**Optimization:** Cut fixed sleeps by 50%, use smart waits:
```python
# Replace fixed sleep with element state checks
await page.wait_for_load_state("domcontentloaded", timeout=15000)
# Only sleep if element NOT ready
```

### 4. Faster OTP Polling (3-5s saved)
**Current:** 3s intervals (line 1170)
**Grok:** 0.5s intervals

**Change:**
```python
# mail_providers/gsuite_imap.py:wait_otp
poll_interval=0.5  # was 3
```

### 5. Longer 9router Poll Timeout (avoid false negatives)
**Current:** 3 retries × exponential = ~14s max (router9_oauth.py:328)
**Grok:** 20 retries × 5s = 100s max

**Change:**
```python
# router9_oauth.py:297
def poll_account(..., max_retries: int = 20):  # was 3
    ...
    time.sleep(5)  # fixed 5s, not exponential
```

### 6. Add Service-Level Retry Loop (like Grok)
**Current:** `service.py:448` — single try, no retry
**Grok:** 3 retries per account with email/OTP reuse

**Add to service.py:332-370:**
```python
async def run_account(...):
    MAX_RETRIES = 3
    cached_email = None
    cached_device_code = None
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if use_9router:
                result = await kiro_register.register_via_9router_oauth(
                    cached_email=cached_email,
                    cached_device_code_data=cached_device_code,
                    ...
                )
            
            # Success if router9_exported=True or incomplete=False
            if not result.get("incomplete"):
                return result
            
            # Cache for retry
            cached_email = result.get("email")
            if "device_code_info" in result:
                cached_device_code = result["device_code_info"]
            
            logger.warning(f"Incomplete registration (attempt {attempt}/{MAX_RETRIES}): {result.get('failReason')}")
            
            # Retry delays: 5s, 10s, 15s
            if attempt < MAX_RETRIES:
                await asyncio.sleep(5 * attempt)
        
        except Exception as e:
            logger.error(f"Registration exception (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(5 * attempt)
    
    # Final failure
    return {"incomplete": True, "failReason": "max retries exceeded"}
```

### 7. Parallel Element Location (2-3s saved per page)
**Grok pattern (line 1685-1731):** Uses `asyncio.gather()` to locate multiple elements simultaneously

**Apply to:**
- Email input + Continue button (signin.aws page)
- OTP input + Continue button
- Password inputs + Continue button

**Example:**
```python
# kiro_register.py:1686-1731 — already optimized in grok
async def find_elements_parallel(page, selectors_dict):
    """Locate multiple elements in parallel."""
    async def find_one(name, selectors):
        for sel in selectors:
            try:
                loc = page.locator(sel)
                await loc.first.wait_for(state="visible", timeout=8000)
                return (name, loc.first, sel)
            except:
                continue
        return (name, None, None)
    
    results = await asyncio.gather(
        *[find_one(name, sels) for name, sels in selectors_dict.items()],
        return_exceptions=True
    )
    return {r[0]: (r[1], r[2]) for r in results if isinstance(r, tuple)}
```

### 8. Remove Mouse Movement Simulation (5-8s saved)
**Current:** Lines 347-357, 917-931, 1183-1191, 1251-1256
**Benefit:** TES doesn't penalize for missing mouse jitter; adds 1-2s per step

**Action:** Comment out or make optional via env var:
```python
ENABLE_MOUSE_JITTER = _env_bool(os.environ.get("ENABLE_MOUSE_JITTER", "false"))
if ENABLE_MOUSE_JITTER:
    await _move_to_element(page, locator)
```

---

## Estimated Speedup

| Change | Time Saved | Implementation Effort |
|--------|-----------|---------------------|
| Fix verification URL | (critical fix) | 5 min |
| Fast form fill | 10-15s | 10 min |
| Reduce sleeps | 30-40s | 15 min |
| Fast OTP polling | 3-5s | 2 min |
| Longer 9router poll | (reliability) | 2 min |
| Service retry loop | (reliability) | 20 min |
| Parallel element search | 6-9s | 15 min |
| Remove mouse jitter | 5-8s | 5 min |

**Total savings:** ~60-80 seconds per account (40% faster)
**From:** ~150s → **To:** ~70-90s

---

## Implementation Priority

1. **Fix verification URL** (critical, blocks functionality)
2. **Fast form fill** (biggest single speedup)
3. **Reduce sleep delays** (easy wins)
4. **Service retry loop** (reliability + cache reuse)
5. **Longer 9router poll** (avoid false negatives)
6. **Fast OTP polling** (small but easy)
7. **Parallel element search** (advanced)
8. **Remove mouse jitter** (optional)
