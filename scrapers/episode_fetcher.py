"""
episode_fetcher.py
──────────────────
MODE 1  fetch_m3u8()       — intercept master.m3u8 from video player
MODE 2  fetch_gdshare_url() — get Gdshare link without ever navigating
                              to the download1 page (bypasses Turnstile on VPS)

KEY FIX for VPS Turnstile on download1:
  Instead of page.goto(download1_url) — which triggers Turnstile on datacenter IPs —
  we use context.request.get() to silently fetch the HTML from within the same
  browser session. CF sees valid cookies → no challenge → Gdshare link extracted
  from the HTML with regex. No navigation, no Turnstile.
"""
import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from config import Config

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:154.0) "
      "Gecko/20100101 Firefox/154.0")

_SS_DIR = Path(Config.SCREENSHOT_DIR)


def _ss(name: str) -> str:
    _SS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")
    return str(_SS_DIR / f"{ts}_{name}.png")


# ── Shared helpers ────────────────────────────────────────────────────────────

async def _has_turnstile(page) -> bool:
    for frame in page.frames:
        if "challenges.cloudflare.com" in frame.url:
            log.warning("  [CF Turnstile] detected in frame: " + frame.url[:60])
            return True
    try:
        if await page.get_by_text("Verify You're Human", exact=False).count() > 0:
            log.warning("  [CF Turnstile] detected via visible text")
            return True
    except Exception:
        pass
    return False


async def _wait_and_find_frame(page):
    video_frame = None
    try:
        iframe_el = await page.wait_for_selector("iframe.player-iframe", timeout=10000)
        if iframe_el:
            video_frame = await iframe_el.content_frame()
            log.info("  ✓ Found video frame via selector")
    except Exception:
        pass
    if not video_frame:
        for frame in page.frames:
            if any(k in frame.url for k in ["p2pplay", "abyssplayer", "playonline"]):
                video_frame = frame
                log.info(f"  ✓ Found video frame via URL: {frame.url[:60]}")
                break
    if not video_frame:
        log.info("  ⚠ Using main page as fallback frame")
        video_frame = page
    return video_frame


async def _safe_goto(page, url: str) -> bool:
    """Navigate to url; handle Turnstile with click+reload retries."""
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(8)

    for attempt in range(1, 4):
        if not await _has_turnstile(page):
            return True
        log.warning(f"  [CF Turnstile] Attempt {attempt}/3 — clicking widget...")
        try:
            cf_iframe = page.locator('iframe[src*="challenges.cloudflare.com"]').first
            if await cf_iframe.count() > 0:
                box = await cf_iframe.bounding_box()
                if box:
                    await page.mouse.click(
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                        delay=200,
                    )
                    await asyncio.sleep(6)
        except Exception:
            pass
        if not await _has_turnstile(page):
            return True
        log.warning("  [CF Turnstile] Still blocked — reloading...")
        await page.reload(wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(8)

    if await _has_turnstile(page):
        log.error("  ❌ Cloudflare Turnstile persists after 3 attempts")
        return False
    return True


def _extract_gdshare_from_html(html: str) -> str | None:
    """Parse Gdshare URL from download1 page HTML."""
    # Pattern 1: direct href to gdshare.top
    m = re.search(r'href=["\']?(https://gdshare\.top/download/[^"\'>\s]+)', html)
    if m:
        return m.group(1)
    # Pattern 2: data-label="Gdshare" before href
    m = re.search(
        r'data-label=["\']Gdshare["\'][^>]*href=["\']([^"\']+)["\']', html
    )
    if m:
        return m.group(1)
    # Pattern 3: href before data-label="Gdshare"
    m = re.search(
        r'href=["\']([^"\']+)["\'][^>]*data-label=["\']Gdshare["\']', html
    )
    if m:
        return m.group(1)
    # Pattern 4: any gdshare.top URL in the HTML
    m = re.search(r'(https://gdshare\.top/[^\s"\'<>]+)', html)
    if m:
        return m.group(1)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# MODE 1 — m3u8 fetch
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_m3u8(anime_url: str) -> str | None:
    m3u8_url   = None
    m3u8_event = asyncio.Event()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=Config.HEADLESS,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=UA, viewport={"width": 1280, "height": 720},
        )
        await Stealth().apply_stealth_async(context)
        page = await context.new_page()

        async def handle_popup(popup):
            log.info("  [Ad Block] Closing redirect tab...")
            try:
                await popup.close()
            except Exception:
                pass
        page.on("popup", lambda pop: asyncio.create_task(handle_popup(pop)))

        async def on_request(request):
            nonlocal m3u8_url
            if "master.m3u8" in request.url and not m3u8_event.is_set():
                m3u8_url = request.url
                log.info(f"\n✅ m3u8 found:\n{request.url}")
                m3u8_event.set()
        page.on("request", on_request)

        log.info("[1/4] Opening anime page...")
        await page.goto(anime_url, wait_until="domcontentloaded", timeout=60000)
        log.info(f"  title: {await page.title()}")

        try:
            await page.wait_for_selector("text=Watch Online", timeout=20000)
            log.info("  ✓ Page loaded")
        except Exception:
            await asyncio.sleep(10)

        log.info("[2/4] Selecting last episode...")
        watch_links = page.get_by_text("Watch Online", exact=True)
        count = await watch_links.count()
        log.info(f"  Found {count} Watch Online links")

        if count == 0:
            log.error("  ❌ No Watch Online links found")
            await browser.close()
            return None

        log.info(f"  Clicking last Watch Online (Episode {count})...")
        await watch_links.last.click(timeout=10000)
        log.info("  ✓ Clicked")
        await asyncio.sleep(5)

        try:
            await page.evaluate("""() => {
                document.querySelectorAll('*').forEach(el => {
                    const s = window.getComputedStyle(el);
                    if (s.zIndex === '2147483647' && s.opacity === '0.01') el.remove();
                });
            }""")
        except Exception:
            pass

        log.info("[3/4] Clicking Fullscreen...")
        try:
            fs_btn = page.locator("button", has_text="Fullscreen").first
            await fs_btn.wait_for(state="visible", timeout=10000)
            await fs_btn.click(force=True, timeout=5000)
            log.info("  ✓ Fullscreen clicked")
        except Exception:
            log.info("  ⚠ Fullscreen not found (ok)")

        await asyncio.sleep(2)

        log.info("[4/4] Locating video frame...")
        video_frame = await _wait_and_find_frame(page)

        for cf_attempt in range(1, 4):
            if not await _has_turnstile(page):
                break
            log.warning(f"  [CF Turnstile] attempt {cf_attempt}/3 — reloading...")
            await page.reload(wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(6)
            try:
                wl = page.get_by_text("Watch Online", exact=True)
                if await wl.count() > 0:
                    await wl.last.click(timeout=10000)
                    await asyncio.sleep(5)
            except Exception:
                pass
            video_frame = await _wait_and_find_frame(page)
        else:
            log.error("  ❌ Turnstile persists — try Mode 2 (direct download)")
            await browser.close()
            return None

        log.info("\n  [!] Waiting 10 seconds for player to settle... [!]")
        await asyncio.sleep(10)

        try:
            await video_frame.evaluate("""() => {
                window.open = () => null;
                document.querySelectorAll('a').forEach(el => {
                    el.removeAttribute('target');
                    el.href = 'javascript:void(0)';
                    el.onclick = e => e.preventDefault();
                });
            }""")
            log.info("  ✓ Ad link traps neutralized")
        except Exception as e:
            log.info(f"  ⚠ Link neutralize: {e}")

        log.info("  Attempting to start playback...")
        for attempt in range(1, 4):
            if m3u8_event.is_set():
                break
            log.info(f"  ▶ Attempt {attempt}/3...")

            try:
                await video_frame.evaluate("""() => {
                    const v = document.querySelector('video');
                    if (v) {
                        v.muted = true;
                        let pp = v.play();
                        if (pp && typeof pp.catch === 'function')
                            pp.catch(e => console.log('play blocked:', e));
                    }
                }""")
                log.info("    → JS video.play() called")
            except Exception as e:
                log.info(f"    → JS play error: {e}")

            try:
                await video_frame.evaluate("""() => {
                    const v = document.querySelector('video');
                    if (v) {
                        const r = v.getBoundingClientRect();
                        const ev = new MouseEvent('click', {
                            bubbles: true, cancelable: true,
                            clientX: r.left + r.width/2, clientY: r.top + r.height/2
                        });
                        v.dispatchEvent(ev);
                        const ov = document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);
                        if (ov && ov !== v) ov.dispatchEvent(new MouseEvent('click',{bubbles:true}));
                    }
                }""")
                log.info("    → JS center click dispatched")
            except Exception as e:
                log.info(f"    → JS click error: {e}")

            for sel in [".jw-icon-display",".jw-display-icon-container",
                        '[aria-label="Play"]',".vjs-big-play-button",
                        ".plyr__control--overlaid","video"]:
                try:
                    el = video_frame.locator(sel).first
                    if await el.count() > 0:
                        await el.click(force=True, timeout=2000)
                        log.info(f"    → CSS click: {sel}")
                        break
                except Exception:
                    continue

            try:
                await video_frame.evaluate("""() => {
                    document.dispatchEvent(new KeyboardEvent('keydown',
                        {key:' ',code:'Space',bubbles:true}));
                }""")
            except Exception:
                pass

            try:
                await asyncio.wait_for(m3u8_event.wait(), timeout=5)
                break
            except asyncio.TimeoutError:
                continue

        if not m3u8_event.is_set():
            log.info("\n  Waiting for m3u8 (up to 30s)...")
            try:
                await asyncio.wait_for(m3u8_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                log.warning("  ⚠ Timeout — m3u8 not captured")
                await page.screenshot(path=_ss("ERR_m3u8_timeout"))

        if m3u8_url:
            log.info(f"\n✅ m3u8 captured: {m3u8_url}")
        else:
            log.error("❌ m3u8 not found")

        await browser.close()
        log.info("  Browser closed ✓")

    return m3u8_url


# ══════════════════════════════════════════════════════════════════════════════
# MODE 2 — 480p GDShare URL
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_gdshare_url(anime_url: str,
                             target_episode: int = None) -> tuple[str | None, int]:
    """
    Strategy (VPS-safe, no Turnstile):
      1. Load main anime page normally (no CF issue here)
      2. Extract download1 URL from DOM (no navigation = no CF)
      3. Fetch download1 HTML silently via context.request.get()
         ↳ Same browser session = CF cookies included = no Turnstile triggered
      4. Parse Gdshare URL from the fetched HTML with regex
      5. Fallback: navigate to download1 if fetch/parse fails
    """
    gdshare_url = None
    episode_num = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=Config.HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=UA, viewport={"width": 1280, "height": 720},
        )
        await Stealth().apply_stealth_async(context)
        page = await context.new_page()

        async def _close_popup(popup):
            try:
                await popup.close()
            except Exception:
                pass
        page.on("popup", lambda pop: asyncio.create_task(_close_popup(pop)))

        # ── Step 1: Load main page (works on VPS — no CF here) ───────────────
        log.info(f"[480p] Opening: {anime_url}")
        if not await _safe_goto(page, anime_url):
            await browser.close()
            return None, 0
        log.info(f"  title: {await page.title()}")

        try:
            await page.wait_for_selector('a:has-text("480p")', timeout=20000)
        except Exception:
            await asyncio.sleep(8)

        # ── Step 2: Extract download1 URL from DOM (no page navigation) ──────
        link_data = await page.evaluate(r'''([targetEp]) => {
            const allLinks = Array.from(document.querySelectorAll('a'));
            const p480 = allLinks.filter(a => a.textContent.trim().toLowerCase().includes("480p"));
            if (p480.length === 0) return {href: null, total: 0, msg: "No 480p links"};
            if (!targetEp) return {href: p480[p480.length-1].href, total: p480.length, msg: "Last link"};

            const epRe = new RegExp("Episode\\s*0*" + targetEp + "\\b", "i");

            for (let a of p480) {
                let par = a.parentElement;
                let foundEp = false, foundMulti = false;
                for (let i = 0; i < 6 && par; i++) {
                    const txt = par.textContent;
                    if ((txt.match(/Episode\s*\d+/gi)||[]).length > 3) break;
                    if (epRe.test(txt)) foundEp = true;
                    if (/multi\s*audio/i.test(txt)) foundMulti = true;
                    if (foundEp && foundMulti)
                        return {href: a.href, total: p480.length,
                                msg: `Ep ${targetEp} + Multi Audio`};
                    par = par.parentElement;
                }
            }
            for (let a of p480) {
                let par = a.parentElement;
                for (let i = 0; i < 6 && par; i++) {
                    const txt = par.textContent;
                    if ((txt.match(/Episode\s*\d+/gi)||[]).length > 3) break;
                    if (epRe.test(txt))
                        return {href: a.href, total: p480.length,
                                msg: `Ep ${targetEp} (no Multi-Audio tag)`};
                    par = par.parentElement;
                }
            }
            return {href: null, total: p480.length, msg: `Ep ${targetEp} not found`};
        }''', [target_episode])

        episode_num   = link_data.get("total", 0)
        download1_url = link_data.get("href")
        log.info(f"  DOM result: {link_data.get('msg')}")

        if not download1_url:
            log.error("  ❌ No 480p download URL found in DOM")
            await browser.close()
            return None, episode_num

        log.info(f"  download1 URL: {download1_url}")

        # ── Step 3: Fetch download1 HTML silently via context.request ─────────
        # This is the key VPS fix:
        #   context.request shares the browser's CF clearance cookies
        #   but does NOT render the page → Turnstile never triggers
        #   CF sees valid session cookies → returns the HTML directly
        log.info("  Fetching download1 HTML via API request (no Turnstile)...")
        try:
            api_resp = await context.request.get(
                download1_url,
                headers={
                    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer":         anime_url,
                    "User-Agent":      UA,
                },
            )
            log.info(f"  API response status: {api_resp.status}")

            if api_resp.ok:
                html = await api_resp.text()
                gdshare_url = _extract_gdshare_from_html(html)

                if gdshare_url:
                    log.info(f"  ✅ Gdshare URL from HTML: {gdshare_url}")
                else:
                    log.warning("  Gdshare pattern not found in HTML — will try page navigation")
            else:
                log.warning(f"  API request returned {api_resp.status} — will try page navigation")

        except Exception as e:
            log.warning(f"  API request failed ({e}) — will try page navigation")

        # ── Step 4: Fallback — navigate to download1 page ────────────────────
        # Only reached if the silent fetch failed (rare on VPS, common locally)
        if not gdshare_url:
            log.info("  Fallback: navigating to download1 page...")
            await page.screenshot(path=_ss("480_fallback_before_nav"))

            if not await _safe_goto(page, download1_url):
                log.error("  ❌ Turnstile blocked download1 navigation")
                await browser.close()
                return None, episode_num

            await asyncio.sleep(3)
            log.info(f"  page title: {await page.title()}")

            gdshare_el = page.locator('a[data-label="Gdshare"]')
            count      = await gdshare_el.count()
            log.info(f"  Gdshare elements on page: {count}")

            if count > 0:
                href = await gdshare_el.first.get_attribute("href") or ""
                if href.startswith("http") and "gdshare" in href:
                    gdshare_url = href
                else:
                    try:
                        async with context.expect_page(timeout=10000) as gp_info:
                            await gdshare_el.first.click()
                        gp = await gp_info.value
                        await asyncio.sleep(2)
                        gdshare_url = gp.url
                        await gp.close()
                    except Exception as ex:
                        log.warning(f"  Tab capture failed: {ex}")
            else:
                log.error("  ❌ Gdshare element not found on page")

        log.info(f"[480p] Final GDShare URL: {gdshare_url}")
        await browser.close()
        log.info("  Browser closed ✓")

    return gdshare_url, episode_num
