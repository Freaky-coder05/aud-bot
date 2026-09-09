"""
cf_bypass.py
────────────
Bypass Cloudflare "Performing security verification" (auto JS challenge).
Extracts:
  - cf_clearance  cookie value  (used for subsequent requests)
  - user-agent    string        (must match the one used to get clearance)

Two methods (tried in order):
  Method A — curl_cffi  (fast, no browser, best TLS fingerprint)
  Method B — Playwright (fallback, browser-based, headed mode works best)

Usage in your code:
    from cf_bypass import get_cf_clearance
    result = await get_cf_clearance("https://animepahe.pw/")
    # result = {"cf_clearance": "...", "user_agent": "...", "method": "A"}
"""
import asyncio
import logging
from typing import Optional

log = logging.getLogger(__name__)

# ── User-Agent used for all requests (keep consistent) ───────────────────────
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


# ══════════════════════════════════════════════════════════════════════════════
# METHOD A — curl_cffi  (fastest, recommended)
# pip install curl_cffi
# ══════════════════════════════════════════════════════════════════════════════

async def _method_a(url: str, timeout: int) -> Optional[dict]:
    """
    Uses curl_cffi which mimics Chrome's TLS fingerprint perfectly.
    Cloudflare's auto-challenge passes automatically.
    """
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        log.warning("[CF-A] curl_cffi not installed — pip install curl_cffi")
        return None

    log.info("[CF-A] Trying curl_cffi (Chrome TLS impersonation)...")
    try:
        async with AsyncSession(impersonate="chrome120") as session:
            resp = await session.get(url, timeout=timeout)

        # Extract cf_clearance from response cookies
        cookies = dict(resp.cookies)
        cf_val  = cookies.get("cf_clearance")

        if cf_val:
            log.info(f"[CF-A] ✅ cf_clearance obtained via curl_cffi")
            return {
                "cf_clearance": cf_val,
                "user_agent":   UA,
                "method":       "curl_cffi",
                "all_cookies":  cookies,
            }
        else:
            log.warning("[CF-A] cf_clearance not in response — trying Method B")
            return None

    except Exception as e:
        log.warning(f"[CF-A] Failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# METHOD B — Playwright  (browser-based fallback)
# ══════════════════════════════════════════════════════════════════════════════

async def _method_b(url: str, timeout: int, headless: bool) -> Optional[dict]:
    """
    Opens a real Chromium browser with stealth, waits for CF to auto-verify,
    polls for the cf_clearance cookie.
    Headed mode (headless=False) has much higher pass rate.
    """
    try:
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth
    except ImportError:
        log.warning("[CF-B] playwright / playwright-stealth not installed")
        return None

    log.info(f"[CF-B] Trying Playwright ({'headless' if headless else 'headed'})...")

    result = None
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1280,720",
            ],
        )
        context = await browser.new_context(
            user_agent   = UA,
            viewport     = {"width": 1280, "height": 720},
            locale       = "en-US",
            timezone_id  = "Asia/Kolkata",
        )
        await Stealth().apply_stealth_async(context)
        page = await context.new_page()

        log.info(f"[CF-B] Navigating to: {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log.warning(f"[CF-B] Navigation error (may be ok): {e}")

        # Poll for cf_clearance cookie — CF auto-challenge takes 2-8 seconds
        log.info(f"[CF-B] Polling for cf_clearance (up to {timeout}s)...")
        for i in range(timeout):
            cookies = await context.cookies()
            cf_cookie = next(
                (c for c in cookies if c["name"] == "cf_clearance"), None
            )
            if cf_cookie:
                all_cookies = {c["name"]: c["value"] for c in cookies}
                log.info(f"[CF-B] ✅ cf_clearance obtained in ~{i}s")
                result = {
                    "cf_clearance": cf_cookie["value"],
                    "user_agent":   UA,
                    "method":       "playwright",
                    "all_cookies":  all_cookies,
                }
                break
            await asyncio.sleep(1)
            if i > 0 and i % 5 == 0:
                log.info(f"[CF-B]   still waiting... ({i}s)")
                # Check if the spinner is gone — page may have loaded
                title = await page.title()
                log.info(f"[CF-B]   page title: {title}")

        if not result:
            log.warning(f"[CF-B] cf_clearance not obtained after {timeout}s")

        await browser.close()

    return result


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

async def get_cf_clearance(
    url: str,
    timeout: int  = 30,
    headless: bool = False,   # headed works better for CF
) -> Optional[dict]:
    """
    Get Cloudflare clearance for `url`.

    Returns dict:
        {
            "cf_clearance": "<cookie value>",
            "user_agent":   "<UA string>",
            "method":       "curl_cffi" | "playwright",
            "all_cookies":  { name: value, ... }
        }
    Returns None if both methods fail.

    The caller MUST use the same user_agent for all subsequent requests,
    otherwise CF will reject the cf_clearance cookie.
    """
    # Method A — curl_cffi (no browser, fast)
    result = await _method_a(url, timeout)
    if result:
        return result

    # Method B — Playwright headed (more reliable against harder challenges)
    result = await _method_b(url, timeout, headless=headless)
    if result:
        return result

    # Method B retry — headless as last resort
    if not headless:
        log.info("[CF] Retrying with headless=True as last resort...")
        result = await _method_b(url, timeout, headless=True)

    return result


# ── Convenience: build a requests-compatible headers dict ────────────────────

def build_headers(clearance_result: dict) -> dict:
    """
    Build headers/cookies dict ready to use with requests / httpx / aiohttp.

    Example:
        result  = await get_cf_clearance("https://animepahe.pw/")
        headers = build_headers(result)
        resp    = requests.get("https://animepahe.pw/anime", **headers)
    """
    if not clearance_result:
        return {}
    cookies_str = "; ".join(
        f"{k}={v}" for k, v in clearance_result.get("all_cookies", {}).items()
    )
    return {
        "headers": {
            "User-Agent": clearance_result["user_agent"],
            "Cookie":     cookies_str,
        },
        "cookies": clearance_result.get("all_cookies", {}),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Quick test — run directly to check if it works
# ══════════════════════════════════════════════════════════════════════════════

async def _test():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")

    TARGET = "https://animepahe.pw/"
    print(f"\nTesting CF bypass on: {TARGET}\n")

    result = await get_cf_clearance(TARGET, timeout=30, headless=False)

    if result:
        print("\n" + "="*60)
        print("✅ SUCCESS")
        print("="*60)
        print(f"Method     : {result['method']}")
        print(f"User-Agent : {result['user_agent']}")
        print(f"cf_clearance:\n  {result['cf_clearance']}")
        print("\nAll cookies:")
        for k, v in result.get("all_cookies", {}).items():
            print(f"  {k} = {v[:60]}{'...' if len(v)>60 else ''}")
        print("="*60)

        # Show how to use it
        h = build_headers(result)
        print("\nUse in requests like this:")
        print(f"  headers = {{'User-Agent': '{result['user_agent'][:50]}...'}}")
        print(f"  cookies = {{'cf_clearance': '{result['cf_clearance'][:30]}...'}}")
    else:
        print("\n❌ FAILED — could not obtain cf_clearance")
        print("Try running with headless=False (visible browser window)")


if __name__ == "__main__":
    asyncio.run(_test())
