"""
episode_fetcher.py
──────────────────
MODE 1  fetch_m3u8()        — Playwright → intercept master.m3u8
MODE 2  fetch_gdshare_url() — Playwright → main page → download1 URL
                              DrissionPage → CF auto-verify → Continue → Gdshare

BUG FIXED:
  Old check: "verifying" not in dp.html  → always True (iframe not in html)
  New check: "verify" not in dp.title    → reliable, title stays "Verify - I'm not a robot"
             until CF actually passes, then changes to the real download page title.
  Strategy:  keep clicking Continue every 2s until title changes away from verify page.
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

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


_SS_DIR = Path(Config.SCREENSHOT_DIR)


def _ss(name: str) -> str:
    _SS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")
    return str(_SS_DIR / f"{ts}_{name}.png")


def _on_verify_page(dp) -> bool:
    """True if DrissionPage is still on the Cloudflare verify page."""
    title = dp.title.lower()
    return "verify" in title or "robot" in title or "i'm not a robot" in title


def _parse_gdshare(html: str) -> str | None:
    for pat in [
        r'href=["\']?(https://gdshare\.top/download/[^"\'>\s]+)',
        r'data-label=["\']Gdshare["\'][^>]*href=["\']([^"\']+)["\']',
        r'href=["\']([^"\']+)["\'][^>]*data-label=["\']Gdshare["\']',
        r'(https://gdshare\.top/[^\s"\'<>]+)',
    ]:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _has_turnstile(page) -> bool:
    for frame in page.frames:
        if "challenges.cloudflare.com" in frame.url:
            return True
    try:
        if await page.get_by_text("Verify You're Human", exact=False).count() > 0:
            return True
    except Exception:
        pass
    return False


async def _find_video_frame(page):
    video_frame = None
    try:
        el = await page.wait_for_selector("iframe.player-iframe", timeout=10000)
        if el:
            video_frame = await el.content_frame()
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
        video_frame = page
    return video_frame


# ══════════════════════════════════════════════════════════════════════════════
# DrissionPage — CF auto-verify + Continue + Gdshare
# ══════════════════════════════════════════════════════════════════════════════
def _drission_get_gdshare(download1_url: str) -> str | None:
    """
    FIXED ORDER:
      1. Navigate
      2. Check if on verify page by TITLE (reliable)
      3. Loop: try clicking Continue every 2s until title changes (CF passed)
      4. Only THEN extract Gdshare URL
    """
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
    except ImportError:
        log.error("[DrissionPage] Not installed — pip install DrissionPage")
        return None

    gdshare_url = None

    try:
        co = ChromiumOptions()
        co.headless(Config.HEADLESS)
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-blink-features=AutomationControlled")

        if getattr(Config, "PROXY_URL", ""):
            co.set_proxy(Config.PROXY_URL)

        dp = ChromiumPage(addr_or_opts=co)

        # ── Step 1: Navigate ──────────────────────────────────────────────────
        log.info(f"  [DrissionPage] Opening: {download1_url}")
        dp.get(download1_url)
        dp.wait(2)
        log.info(f"  [DrissionPage] Initial title: {dp.title}")

        # ── Step 2 + 3: Wait for CF → keep clicking Continue ──────────────────
        # Title stays "Verify - I'm not a robot" until CF auto-verify completes.
        # We click Continue every 2s — it's a no-op until CF passes,
        # then it works and the title changes to the real download page.
        # DO NOT click Continue before this loop — it will always fail.

        def _on_verify_page() -> bool:
            t = dp.title.lower()
            return "verify" in t or "robot" in t or "just a moment" in t or "cloudflare" in t

        if _on_verify_page():
            log.info("  [DrissionPage] CF verify page — waiting to pass...")

            for i in range(40):  # max 80 seconds
                if not _on_verify_page():
                    log.info(f"  [DrissionPage] ✅ CF passed in ~{i*2}s — title: {dp.title}")
                    break

                # Click Continue (no-op until CF verifies, then it redirects)
                try:
                    btn = dp.ele("text:Continue", timeout=1)
                    if btn:
                        btn.click()
                        log.info(f"  [DrissionPage] [{i*2}s] Continue clicked, title: {dp.title}")
                except Exception:
                    pass

                dp.wait(2)
            else:
                log.error("  [DrissionPage] ❌ CF did not pass in 80s")
                try:
                    dp.get_screenshot(path=_ss("drission_ERR_verify_timeout"))
                except Exception:
                    pass
                dp.quit()
                return None  # ← exit early, don't try to extract Gdshare
        else:
            log.info(f"  [DrissionPage] No verify page — direct access")

        # ── Step 4: Extract Gdshare URL (only reached after CF passed) ─────────
        log.info(f"  [DrissionPage] On page: {dp.title}")
        log.info("  [DrissionPage] Extracting Gdshare URL...")

        # Method 1: data-label attribute
        try:
            gd = dp.ele('[data-label="Gdshare"]', timeout=5)
            if gd:
                href = gd.attr("href") or ""
                if href.startswith("http") and "gdshare" in href:
                    gdshare_url = href
                    log.info(f"  [DrissionPage] ✅ Gdshare attr: {gdshare_url}")
        except Exception:
            pass

        # Method 2: text search
        if not gdshare_url:
            try:
                gd = dp.ele("text:Gdshare", timeout=5)
                if gd:
                    href = gd.attr("href") or ""
                    if "gdshare" in href:
                        gdshare_url = href
                        log.info(f"  [DrissionPage] ✅ Gdshare text: {gdshare_url}")
            except Exception:
                pass

        # Method 3: HTML regex
        if not gdshare_url:
            gdshare_url = _parse_gdshare(dp.html)
            if gdshare_url:
                log.info(f"  [DrissionPage] ✅ Gdshare HTML: {gdshare_url}")

        if not gdshare_url:
            log.error(f"  [DrissionPage] ❌ Gdshare not found — title: {dp.title}")
            try:
                dp.get_screenshot(path=_ss("drission_ERR_no_gdshare"))
            except Exception:
                pass

        dp.quit()

    except Exception as e:
        log.error(f"  [DrissionPage] Fatal: {e}")
        try:
            dp.quit()
        except Exception:
            pass

    return gdshare_url


# ══════════════════════════════════════════════════════════════════════════════
# MODE 1 — m3u8
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_m3u8(anime_url: str) -> str | None:
    m3u8_url   = None
    m3u8_event = asyncio.Event()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=Config.HEADLESS,
            args=["--start-maximized",
                  "--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=UA, viewport={"width": 1280, "height": 720},
        )
        await Stealth().apply_stealth_async(context)
        page = await context.new_page()

        page.on("popup", lambda pop: asyncio.create_task(
            pop.close() if not pop.is_closed() else asyncio.sleep(0)
        ))

        async def on_request(req):
            nonlocal m3u8_url
            if "master.m3u8" in req.url and not m3u8_event.is_set():
                m3u8_url = req.url
                log.info(f"✅ m3u8: {req.url}")
                m3u8_event.set()
        page.on("request", on_request)

        log.info("[1/4] Opening anime page...")
        await page.goto(anime_url, wait_until="domcontentloaded", timeout=60000)
        log.info(f"  title: {await page.title()}")
        try:
            await page.wait_for_selector("text=Watch Online", timeout=20000)
        except Exception:
            await asyncio.sleep(10)

        log.info("[2/4] Clicking last Watch Online...")
        links = page.get_by_text("Watch Online", exact=True)
        n = await links.count()
        if n == 0:
            log.error("❌ No Watch Online links")
            await browser.close()
            return None
        await links.last.click(timeout=10000)
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
            fs = page.locator("button", has_text="Fullscreen").first
            await fs.wait_for(state="visible", timeout=10000)
            await fs.click(force=True, timeout=5000)
        except Exception:
            log.info("  ⚠ Fullscreen not found (ok)")
        await asyncio.sleep(2)

        log.info("[4/4] Locating video frame...")
        video_frame = await _find_video_frame(page)

        for attempt in range(1, 4):
            if not await _has_turnstile(page):
                break
            log.warning(f"  [CF] Turnstile attempt {attempt}/3 — reloading...")
            await page.reload(wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(6)
            try:
                wl = page.get_by_text("Watch Online", exact=True)
                if await wl.count() > 0:
                    await wl.last.click(timeout=10000)
                    await asyncio.sleep(5)
            except Exception:
                pass
            video_frame = await _find_video_frame(page)
        else:
            log.error("❌ Turnstile persists")
            await browser.close()
            return None

        log.info("  Waiting 10s for player to settle...")
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
        except Exception:
            pass

        for attempt in range(1, 4):
            if m3u8_event.is_set():
                break
            log.info(f"  ▶ Attempt {attempt}/3...")
            try:
                await video_frame.evaluate("""() => {
                    const v = document.querySelector('video');
                    if (v) { v.muted = true; let pp = v.play();
                        if (pp?.catch) pp.catch(e => {}); }
                }""")
            except Exception:
                pass
            try:
                await video_frame.evaluate("""() => {
                    const v = document.querySelector('video');
                    if (v) {
                        const r = v.getBoundingClientRect();
                        v.dispatchEvent(new MouseEvent('click', {
                            bubbles: true,
                            clientX: r.left + r.width  / 2,
                            clientY: r.top  + r.height / 2
                        }));
                    }
                }""")
            except Exception:
                pass
            for sel in [".jw-icon-display", '[aria-label="Play"]',
                        ".vjs-big-play-button", ".plyr__control--overlaid", "video"]:
                try:
                    el = video_frame.locator(sel).first
                    if await el.count() > 0:
                        await el.click(force=True, timeout=2000)
                        break
                except Exception:
                    continue
            try:
                await asyncio.wait_for(m3u8_event.wait(), timeout=5)
                break
            except asyncio.TimeoutError:
                continue

        if not m3u8_event.is_set():
            try:
                await asyncio.wait_for(m3u8_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                log.warning("⚠ Timeout")
                await page.screenshot(path=_ss("ERR_m3u8_timeout"))

        log.info(f"{'✅ m3u8: ' + m3u8_url if m3u8_url else '❌ m3u8 not found'}")
        await browser.close()
    return m3u8_url


# ══════════════════════════════════════════════════════════════════════════════
# MODE 2 — 480p GDShare
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_gdshare_url(anime_url: str,
                             target_episode: int = None) -> tuple[str | None, int]:
    """
    Step 1: Playwright → main page → extract download1 URL from DOM
    Step 2: DrissionPage → download1 → CF verify → Continue → Gdshare URL
    """
    episode_num   = 0
    download1_url = None

    # ── Step 1: Playwright ────────────────────────────────────────────────────
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

        page.on("popup", lambda pop: asyncio.create_task(
            pop.close() if not pop.is_closed() else asyncio.sleep(0)
        ))

        log.info(f"[480p] Opening main page: {anime_url}")
        await page.goto(anime_url, wait_until="domcontentloaded", timeout=60000)
        log.info(f"  title: {await page.title()}")

        try:
            await page.wait_for_selector('a:has-text("480p")', timeout=20000)
        except Exception:
            await asyncio.sleep(8)

        link_data = await page.evaluate(r'''([ep]) => {
            const all480 = Array.from(document.querySelectorAll('a'))
                .filter(a => a.textContent.trim().toLowerCase().includes("480p"));
            if (!all480.length) return {href:null, total:0, msg:"No 480p links"};
            if (!ep) return {href:all480[all480.length-1].href,
                             total:all480.length, msg:"Last"};
            const re = new RegExp("Episode\\s*0*"+ep+"\\b","i");
            for (let a of all480) {
                let par=a.parentElement, hasEp=false, hasMulti=false;
                for (let i=0;i<6&&par;i++) {
                    const t=par.textContent;
                    if ((t.match(/Episode\s*\d+/gi)||[]).length>3) break;
                    if (re.test(t)) hasEp=true;
                    if (/multi\s*audio/i.test(t)) hasMulti=true;
                    if (hasEp&&hasMulti) return {href:a.href,total:all480.length,
                                                  msg:`Ep${ep}+Multi`};
                    par=par.parentElement;
                }
            }
            for (let a of all480) {
                let par=a.parentElement;
                for (let i=0;i<6&&par;i++) {
                    const t=par.textContent;
                    if ((t.match(/Episode\s*\d+/gi)||[]).length>3) break;
                    if (re.test(t)) return {href:a.href,total:all480.length,
                                             msg:`Ep${ep} no-multi`};
                    par=par.parentElement;
                }
            }
            return {href:null, total:all480.length, msg:`Ep${ep} not found`};
        }''', [target_episode])

        episode_num   = link_data.get("total", 0)
        download1_url = link_data.get("href")
        log.info(f"  DOM: {link_data.get('msg')} | total={episode_num}")
        await browser.close()

    if not download1_url:
        log.error("❌ No download1 URL found")
        return None, episode_num

    log.info(f"  download1 URL: {download1_url}")

    # ── Step 2: DrissionPage ──────────────────────────────────────────────────
    loop        = asyncio.get_event_loop()
    gdshare_url = await loop.run_in_executor(
        None, _drission_get_gdshare, download1_url
    )

    log.info(f"[480p] Final GDShare URL: {gdshare_url}")
    return gdshare_url, episode_num
