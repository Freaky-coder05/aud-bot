"""
episode_fetcher.py
──────────────────
Fixes for Koyeb VPS 403:
  1. Dockerfile: official Playwright image (all Chrome deps pre-installed)
  2. context.request.get() → 403 on datacenter IPs because it uses Playwright's
     internal HTTP client (not Chrome TLS). Fix: block CF Turnstile iframe via
     page.route() → navigate download1 normally → Turnstile UI never loads →
     page content (Gdshare link) is accessible directly.
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
        log.info("  ⚠ Using main page as fallback frame")
        video_frame = page
    return video_frame


def _parse_gdshare(html: str) -> str | None:
    """Extract Gdshare URL from download1 page HTML."""
    patterns = [
        r'href=["\']?(https://gdshare\.top/download/[^"\'>\s]+)',
        r'data-label=["\']Gdshare["\'][^>]*href=["\']([^"\']+)["\']',
        r'href=["\']([^"\']+)["\'][^>]*data-label=["\']Gdshare["\']',
        r'(https://gdshare\.top/[^\s"\'<>]+)',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


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
        n     = await links.count()
        if n == 0:
            log.error("❌ No Watch Online links"); await browser.close(); return None
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
            log.error("❌ Turnstile persists — try Mode 2"); await browser.close(); return None

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
                    if (v) { v.muted = true; let p = v.play();
                        if (p?.catch) p.catch(e => {}); }
                }""")
            except Exception:
                pass
            try:
                await video_frame.evaluate("""() => {
                    const v = document.querySelector('video');
                    if (v) {
                        const r = v.getBoundingClientRect();
                        v.dispatchEvent(new MouseEvent('click', {
                            bubbles:true, clientX: r.left+r.width/2, clientY: r.top+r.height/2
                        }));
                    }
                }""")
            except Exception:
                pass
            for sel in [".jw-icon-display",'[aria-label="Play"]',
                        ".vjs-big-play-button",".plyr__control--overlaid","video"]:
                try:
                    el = video_frame.locator(sel).first
                    if await el.count() > 0:
                        await el.click(force=True, timeout=2000); break
                except Exception:
                    continue
            try:
                await asyncio.wait_for(m3u8_event.wait(), timeout=5); break
            except asyncio.TimeoutError:
                continue

        if not m3u8_event.is_set():
            try:
                await asyncio.wait_for(m3u8_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                log.warning("⚠ Timeout"); await page.screenshot(path=_ss("ERR_m3u8_timeout"))

        log.info(f"{'✅ m3u8: '+m3u8_url if m3u8_url else '❌ m3u8 not found'}")
        await browser.close()
    return m3u8_url


# ══════════════════════════════════════════════════════════════════════════════
# MODE 2 — 480p GDShare
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_gdshare_url(anime_url: str,
                             target_episode: int = None) -> tuple[str | None, int]:
    """
    VPS/Koyeb-safe flow — no Turnstile:

    Step 1: Load main anime page (no CF on this domain)
    Step 2: Extract download1 URL from DOM (no navigation)
    Step 3: Open download1 in the SAME page but with CF challenge BLOCKED via
            page.route() — Turnstile iframe is aborted before it loads,
            so the challenge never appears. The download1 page content
            (Gdshare link) is accessible underneath.
    Step 4: Parse Gdshare URL from page HTML/DOM
    Fallback: context.request.get() if route approach returns no link
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

        page.on("popup", lambda pop: asyncio.create_task(
            pop.close() if not pop.is_closed() else asyncio.sleep(0)
        ))

        # ── Step 1: Main page (no CF) ─────────────────────────────────────────
        log.info(f"[480p] Opening: {anime_url}")
        await page.goto(anime_url, wait_until="domcontentloaded", timeout=60000)
        log.info(f"  title: {await page.title()}")
        try:
            await page.wait_for_selector('a:has-text("480p")', timeout=20000)
        except Exception:
            await asyncio.sleep(8)

        # ── Step 2: Extract download1 URL from DOM ────────────────────────────
        link_data = await page.evaluate(r'''([ep]) => {
            const all480 = Array.from(document.querySelectorAll('a'))
                .filter(a => a.textContent.trim().toLowerCase().includes("480p"));
            if (!all480.length) return {href:null, total:0, msg:"No 480p links"};
            if (!ep) return {href:all480[all480.length-1].href, total:all480.length, msg:"Last"};
            const re = new RegExp("Episode\\s*0*"+ep+"\\b","i");
            for (let a of all480) {
                let par=a.parentElement, hasEp=false, hasMulti=false;
                for (let i=0;i<6&&par;i++) {
                    const t=par.textContent;
                    if ((t.match(/Episode\s*\d+/gi)||[]).length>3) break;
                    if (re.test(t)) hasEp=true;
                    if (/multi\s*audio/i.test(t)) hasMulti=true;
                    if (hasEp&&hasMulti) return {href:a.href,total:all480.length,msg:`Ep${ep}+Multi`};
                    par=par.parentElement;
                }
            }
            for (let a of all480) {
                let par=a.parentElement;
                for (let i=0;i<6&&par;i++) {
                    const t=par.textContent;
                    if ((t.match(/Episode\s*\d+/gi)||[]).length>3) break;
                    if (re.test(t)) return {href:a.href,total:all480.length,msg:`Ep${ep} no-multi`};
                    par=par.parentElement;
                }
            }
            return {href:null, total:all480.length, msg:`Ep${ep} not found`};
        }''', [target_episode])

        episode_num   = link_data.get("total", 0)
        download1_url = link_data.get("href")
        log.info(f"  DOM: {link_data.get('msg')} | total={episode_num}")

        if not download1_url:
            log.error("❌ No download1 URL"); await browser.close(); return None, episode_num

        log.info(f"  download1: {download1_url}")

        # ── Step 3: Block CF Turnstile then navigate to download1 ─────────────
        # KEY FIX for VPS 403:
        # We abort ALL requests to challenges.cloudflare.com BEFORE navigating.
        # Result: the Turnstile iframe never loads → no challenge shown →
        # the download1 page content renders normally with Gdshare link visible.
        log.info("  Routing: blocking CF challenge scripts...")

        async def _block_cf(route):
            await route.abort()

        await page.route("**challenges.cloudflare.com**", _block_cf)
        await page.route("**cf-turnstile**", _block_cf)

        log.info("  Navigating to download1 (CF challenge blocked)...")
        try:
            await page.goto(download1_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log.info(f"  Nav warning (ok): {e}")

        await asyncio.sleep(3)
        log.info(f"  title: {await page.title()}")

        # Check that Turnstile is actually gone
        if await _has_turnstile(page):
            log.warning("  Turnstile still present even with route blocking")
            await page.screenshot(path=_ss("480_turnstile_persists"))

        # ── Step 4a: Try DOM selector first ──────────────────────────────────
        gdshare_el = page.locator('a[data-label="Gdshare"]')
        gd_count   = await gdshare_el.count()
        log.info(f"  Gdshare elements: {gd_count}")

        if gd_count > 0:
            href = await gdshare_el.first.get_attribute("href") or ""
            if href.startswith("http") and "gdshare" in href:
                gdshare_url = href
                log.info(f"  ✅ Gdshare from DOM attr: {gdshare_url}")
            else:
                try:
                    async with context.expect_page(timeout=8000) as gp_info:
                        await gdshare_el.first.click()
                    gp = await gp_info.value
                    await asyncio.sleep(2)
                    gdshare_url = gp.url
                    await gp.close()
                    log.info(f"  ✅ Gdshare from tab: {gdshare_url}")
                except Exception as e:
                    log.warning(f"  Tab capture failed: {e}")

        # ── Step 4b: Fallback — parse raw HTML ────────────────────────────────
        if not gdshare_url:
            log.info("  Parsing Gdshare from page HTML...")
            html       = await page.content()
            gdshare_url = _parse_gdshare(html)
            if gdshare_url:
                log.info(f"  ✅ Gdshare from HTML regex: {gdshare_url}")

        # ── Step 4c: Fallback — context.request (works on non-VPS) ───────────
        if not gdshare_url:
            log.info("  Fallback: context.request.get()...")
            try:
                resp = await context.request.get(
                    download1_url,
                    headers={
                        "Accept":         "text/html,*/*;q=0.8",
                        "Accept-Language":"en-US,en;q=0.9",
                        "Referer":        anime_url,
                        "User-Agent":     UA,
                    },
                )
                log.info(f"  context.request status: {resp.status}")
                if resp.ok:
                    gdshare_url = _parse_gdshare(await resp.text())
                    if gdshare_url:
                        log.info(f"  ✅ Gdshare from request: {gdshare_url}")
            except Exception as e:
                log.warning(f"  context.request failed: {e}")

        if not gdshare_url:
            log.error("❌ Could not extract Gdshare URL by any method")
            await page.screenshot(path=_ss("ERR_no_gdshare"))

        log.info(f"[480p] Final: {gdshare_url}")
        await browser.close()
        log.info("  Browser closed ✓")

    return gdshare_url, episode_num
