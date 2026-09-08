"""
episode_fetcher.py
──────────────────
MODE 1  fetch_m3u8(url)         — intercept master.m3u8 from video player
MODE 2  fetch_gdshare_url(url)  — extract 480p href → download1 → Gdshare URL

Key fix: play() via JavaScript instead of CSS-selector clicks.
The custom "DB" player on p2pplay doesn't match any standard player selectors.
Also fixed: ad-neutralizer no longer removes the play overlay (only redirecting <a> tags).
Browser always closes after task completes (success or timeout).
"""
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from config import Config

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36")

_SS_DIR = Path(Config.SCREENSHOT_DIR)


def _ss(name: str) -> str:
    _SS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")
    return str(_SS_DIR / f"{ts}_{name}.png")


# ══════════════════════════════════════════════════════════════════════════════
# MODE 1 — m3u8 fetch
# ══════════════════════════════════════════════════════════════════════════════



# ── Cloudflare Turnstile detection ────────────────────────────────────────────

async def _has_turnstile(page) -> bool:
    """
    Return True if Cloudflare Turnstile ('Verify You're Human') is blocking
    any frame on the page.  The challenge lives in a nested iframe whose URL
    contains 'challenges.cloudflare.com'.
    """
    for frame in page.frames:
        if "challenges.cloudflare.com" in frame.url:
            log.warning("  [CF Turnstile] detected in frame: " + frame.url[:60])
            return True
    # Fallback: look for the visible text anywhere on page
    try:
        count = await page.get_by_text("Verify You're Human", exact=False).count()
        if count > 0:
            log.warning("  [CF Turnstile] detected via visible text")
            return True
    except Exception:
        pass
    return False


async def _wait_and_find_frame(page):
    """Try to locate the video player frame; return it or None."""
    video_frame = None
    try:
        iframe_el = await page.wait_for_selector("iframe.player-iframe", timeout=10000)
        if iframe_el:
            video_frame = await iframe_el.content_frame()
            log.info("  ✓ Found video frame via selector (iframe.player-iframe)")
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
            user_agent=UA,
            viewport={"width": 1280, "height": 720},
        )
        await Stealth().apply_stealth_async(context)
        page = await context.new_page()

        # Auto-close popup/redirect tabs
        async def handle_popup(popup):
            log.info("  [Ad Block] Closing redirect tab...")
            try:
                await popup.close()
            except Exception:
                pass
        page.on("popup", lambda pop: asyncio.create_task(handle_popup(pop)))

        # Intercept m3u8
        async def on_request(request):
            nonlocal m3u8_url
            if "master.m3u8" in request.url and not m3u8_event.is_set():
                m3u8_url = request.url
                log.info(f"\n✅ m3u8 found:\n{request.url}")
                m3u8_event.set()
        page.on("request", on_request)

        # ── STEP 1: Load main page ────────────────────────────────────────────
        log.info("[1/4] Opening anime page...")
        await page.goto(anime_url, wait_until="domcontentloaded", timeout=60000)
        # await page.screenshot(path=_ss("01_page_loaded"))
        log.info(f"  title: {await page.title()}")

        try:
            await page.wait_for_selector("text=Watch Online", timeout=20000)
            log.info("  ✓ Page loaded")
        except Exception:
            log.info("  ⚠ Waiting 10s...")
            await asyncio.sleep(10)

        # ── STEP 2: Click LAST Watch Online ───────────────────────────────────
        log.info("[2/4] Selecting last episode...")
        watch_links = page.get_by_text("Watch Online", exact=True)
        count = await watch_links.count()
        log.info(f"  Found {count} Watch Online links")

        if count == 0:
            # await page.screenshot(path=_ss("ERR_no_watch_online"))
            log.error("  ❌ No Watch Online links found")
            await browser.close()
            return None

        log.info(f"  Clicking last Watch Online (Episode {count})...")
        await watch_links.last.click(timeout=10000)
        log.info("  ✓ Clicked")
        await asyncio.sleep(5)
        # await page.screenshot(path=_ss("02_after_watch_click"))

        # Clear only truly-invisible ghost overlays
        try:
            await page.evaluate("""() => {
                document.querySelectorAll('*').forEach(el => {
                    const s = window.getComputedStyle(el);
                    if (s.zIndex === '2147483647' && s.opacity === '0.01')
                        el.remove();
                });
            }""")
        except Exception:
            pass

        # ── STEP 3: Fullscreen (best-effort) ──────────────────────────────────
        log.info("[3/4] Clicking Fullscreen...")
        try:
            fs_btn = page.locator("button", has_text="Fullscreen").first
            await fs_btn.wait_for(state="visible", timeout=10000)
            await fs_btn.click(force=True, timeout=5000)
            log.info("  ✓ Fullscreen clicked")
        except Exception:
            log.info("  ⚠ Fullscreen not found (ok — continuing)")

        await asyncio.sleep(2)
        # await page.screenshot(path=_ss("03_after_fullscreen"))

        # ── STEP 4: Find video frame + handle Turnstile ───────────────────────
        log.info("[4/4] Locating video frame...")
        video_frame = await _wait_and_find_frame(page)

        # ── Cloudflare Turnstile loop ─────────────────────────────────────────
        for cf_attempt in range(1, 4):
            if not await _has_turnstile(page):
                break   # No Turnstile — proceed normally

            log.warning(
                f"  [CF Turnstile] attempt {cf_attempt}/3 — "
                "reloading page to clear it..."
            )
            # await page.screenshot(path=_ss(f"CF_turnstile_{cf_attempt}"))
            await page.reload(wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(6)

            # Re-click Watch Online after reload
            try:
                wl = page.get_by_text("Watch Online", exact=True)
                c  = await wl.count()
                if c > 0:
                    await wl.last.click(timeout=10000)
                    await asyncio.sleep(5)
            except Exception:
                pass

            # Re-find frame
            video_frame = await _wait_and_find_frame(page)
        else:
            log.error(
                "  ❌ Cloudflare Turnstile persists after 3 reloads.\n"
                "     m3u8 mode is blocked for now. "
                "Try again later OR switch to Mode 2 (direct download)."
            )
            # await page.screenshot(path=_ss("ERR_cf_turnstile_persistent"))
            await browser.close()
            return None

        # ── Wait 10s (original Cloudflare notice) ─────────────────────────────
        log.info("\n  [!] Waiting 10 seconds for player to settle... [!]")
        await asyncio.sleep(10)
        # await page.screenshot(path=_ss("04_cf_wait_done"))

        # ── Neutralize ONLY ad <a> redirect links ─────────────────────────────
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

        # ── Play retry loop — 3 strategies ───────────────────────────────────
        log.info("  Attempting to start playback...")

        for attempt in range(1, 4):
            if m3u8_event.is_set():
                break
            log.info(f"  ▶ Attempt {attempt}/3...")

            # Strategy 1 — direct JS play()
            # FIX: Removed async/await so Playwright doesn't hang if the video fails to buffer
            try:
                await video_frame.evaluate("""() => {
                    const v = document.querySelector('video');
                    if (v) { 
                        v.muted = true; 
                        let p = v.play();
                        if (p && typeof p.catch === 'function') {
                            p.catch(e => console.log('play blocked or failed'));
                        }
                    }
                }""")
                log.info("    → JS video.play() called")
            except Exception as e:
                log.info(f"    → JS play error: {e}")

            # Strategy 2 — JS dispatchEvent click at video center
            try:
                await video_frame.evaluate("""() => {
                    const v = document.querySelector('video');
                    if (v) {
                        const r  = v.getBoundingClientRect();
                        const cx = r.left + r.width  / 2;
                        const cy = r.top  + r.height / 2;
                        const ev = new MouseEvent('click', {
                            bubbles: true, cancelable: true,
                            clientX: cx, clientY: cy
                        });
                        v.dispatchEvent(ev);
                        // Also try clicking the parent overlay if any
                        const overlay = document.elementFromPoint(cx, cy);
                        if (overlay && overlay !== v)
                            overlay.dispatchEvent(ev.constructor.prototype
                                ? new MouseEvent('click',{bubbles:true}) : ev);
                    }
                }""")
                log.info("    → JS center click dispatched")
            except Exception as e:
                log.info(f"    → JS click error: {e}")

            # Strategy 3 — CSS selectors
            css_sels = [
                ".jw-icon-display", ".jw-display-icon-container",
                '[aria-label="Play"]', ".vjs-big-play-button",
                ".plyr__control--overlaid", "video",
            ]
            for sel in css_sels:
                try:
                    el = video_frame.locator(sel).first
                    if await el.count() > 0:
                        await el.click(force=True, timeout=2000)
                        log.info(f"    → CSS click: {sel}")
                        break
                except Exception:
                    continue

            # Strategy 4 — Spacebar via JS
            try:
                await video_frame.evaluate("""() => {
                    document.dispatchEvent(new KeyboardEvent('keydown',
                        {key:' ', code:'Space', bubbles:true}));
                }""")
                log.info("    → Spacebar dispatched")
            except Exception:
                pass

            try:
                await asyncio.wait_for(m3u8_event.wait(), timeout=5)
                break
            except asyncio.TimeoutError:
                # await page.screenshot(path=_ss(f"play_attempt_{attempt}"))
                continue

        # ── Final 30s wait ────────────────────────────────────────────────────
        if not m3u8_event.is_set():
            log.info("\n  Waiting for m3u8 (up to 30s)...")
            try:
                await asyncio.wait_for(m3u8_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                log.warning("  ⚠ Timeout — m3u8 not captured")
                # await page.screenshot(path=_ss("ERR_m3u8_timeout"))

        if m3u8_url:
            log.info(f"\n✅ m3u8 captured: {m3u8_url}")
        else:
            log.error("❌ m3u8 not found")

        await browser.close()
        log.info("  Browser closed ✓")

    return m3u8_url

# async def fetch_m3u8(anime_url: str) -> str | None:
#     m3u8_url   = None
#     m3u8_event = asyncio.Event()

#     async with async_playwright() as p:
#         browser = await p.chromium.launch(
#             headless=Config.HEADLESS,
#             args=[
#                 "--start-maximized",
#                 "--disable-blink-features=AutomationControlled",
#                 "--no-sandbox",
#                 "--disable-dev-shm-usage",
#             ],
#         )
#         context = await browser.new_context(
#             user_agent=UA,
#             viewport={"width": 1280, "height": 720},
#         )
#         await Stealth().apply_stealth_async(context)
#         page = await context.new_page()

#         # ── Auto-close popup/redirect tabs ────────────────────────────────────
#         async def handle_popup(popup):
#             log.info("  [Ad Block] Intercepted a redirect/pop-up tab! Closing it...")
#             try:
#                 await popup.close()
#             except Exception:
#                 pass

#         page.on("popup", lambda popup: asyncio.create_task(handle_popup(popup)))

#         # ── Intercept m3u8 ────────────────────────────────────────────────────
#         async def on_request(request):
#             nonlocal m3u8_url
#             if "master.m3u8" in request.url and not m3u8_event.is_set():
#                 m3u8_url = request.url
#                 log.info(f"\n✅ m3u8 found:\n{request.url}")
#                 m3u8_event.set()

#         page.on("request", on_request)

#         # ── STEP 1: Load page ─────────────────────────────────────────────────
#         log.info("[1/4] Opening anime page...")
#         await page.goto(anime_url, wait_until="domcontentloaded", timeout=60000)
#         await page.screenshot(path=_ss("01_page_loaded"))
#         log.info(f"  Screenshot saved | title: {await page.title()}")

#         try:
#             await page.wait_for_selector("text=Watch Online", timeout=20000)
#             log.info("  ✓ Page loaded")
#         except Exception:
#             log.info("  ⚠ Waiting 10s more...")
#             await asyncio.sleep(10)
#             await page.screenshot(path=_ss("01b_page_wait"))

#         # ── STEP 2: Click LAST Watch Online ───────────────────────────────────
#         log.info("[2/4] Selecting last episode...")
#         watch_links = page.get_by_text("Watch Online", exact=True)
#         count = await watch_links.count()
#         log.info(f"  Found {count} Watch Online links")

#         if count == 0:
#             await page.screenshot(path=_ss("ERR_no_watch_online"))
#             log.error("  ❌ No Watch Online links found")
#             await browser.close()
#             return None

#         log.info(f"  Clicking last Watch Online (Episode {count})...")
#         await watch_links.last.click(timeout=10000)
#         log.info("  ✓ Last episode Watch Online clicked")
#         await asyncio.sleep(5)
#         await page.screenshot(path=_ss("02_after_watch_click"))

#         # ── Remove invisible ad overlays (ONLY opacity-zero ghost elements) ───
#         # NOTE: We do NOT remove high-z-index elements here anymore — that was
#         # deleting the video player's own play button overlay.
#         log.info("  Clearing ghost ad overlays on the main page...")
#         try:
#             await page.evaluate("""() => {
#                 document.querySelectorAll('*').forEach(el => {
#                     const s = window.getComputedStyle(el);
#                     if (s.zIndex === '2147483647' && s.opacity === '0.01')
#                         el.remove();
#                 });
#             }""")
#             log.info("  ✓ Cleared ghost overlays")
#         except Exception as e:
#             log.info(f"  ⚠ Overlay clear: {e}")

#         # ── STEP 3: Click Fullscreen ──────────────────────────────────────────
#         log.info("[3/4] Clicking Fullscreen on main page...")
#         try:
#             fs_btn = page.locator("button", has_text="Fullscreen").first
#             await fs_btn.wait_for(state="visible", timeout=10000)
#             await fs_btn.click(force=True, timeout=5000)
#             log.info("  ✓ Clicked Fullscreen button")
#         except Exception as e:
#             log.info(f"  ⚠ Fullscreen not clicked: {e}")

#         await asyncio.sleep(2)
#         await page.screenshot(path=_ss("03_after_fullscreen"))

#         # ── STEP 4: Locate video frame ────────────────────────────────────────
#         log.info("[4/4] Locating video frame...")
#         video_frame = None

#         try:
#             iframe_el = await page.wait_for_selector("iframe.player-iframe", timeout=10000)
#             if iframe_el:
#                 video_frame = await iframe_el.content_frame()
#                 log.info("  ✓ Found nested video frame via selector")
#         except Exception:
#             pass

#         if not video_frame:
#             for frame in page.frames:
#                 if any(k in frame.url for k in ["p2pplay", "abyssplayer", "playonline"]):
#                     video_frame = frame
#                     log.info(f"  ✓ Found video frame via URL: {frame.url[:60]}")
#                     break

#         if not video_frame:
#             log.info("  ⚠ Using main page as fallback frame")
#             video_frame = page

#         # ── Cloudflare wait ───────────────────────────────────────────────────
#         log.info("\n  [!] Waiting 10 seconds. IF YOU SEE CLOUDFLARE, SOLVE IT NOW! [!]")
#         await asyncio.sleep(10)
#         await page.screenshot(path=_ss("04_cf_wait_done"))

#         # ── Neutralize ONLY redirecting <a> tags inside the player ────────────
#         # FIX: Previously removed all high-z-index elements, which killed the
#         # play button. Now we only kill <a> tags that would open ads.
#         log.info("\n  Neutralizing ad link traps inside the video player...")
#         try:
#             await video_frame.evaluate("""() => {
#                 window.open = function() { return null; };
#                 document.querySelectorAll('a').forEach(el => {
#                     el.removeAttribute('target');
#                     el.href = 'javascript:void(0)';
#                     el.onclick = e => e.preventDefault();
#                 });
#             }""")
#             log.info("  ✓ Ad link traps neutralized")
#         except Exception as e:
#             log.info(f"  ⚠ Link neutralize: {e}")

#         # ── Click Play — 4-strategy retry loop ───────────────────────────────
#         # Strategy 1: JS video.play()      ← works on ANY player incl. custom DB player
#         # Strategy 2: Click video center   ← coordinate-based, layout-independent
#         # Strategy 3: CSS selectors        ← JWPlayer / VideoJS / Plyr fallback
#         # Strategy 4: Spacebar             ← keyboard fallback

#         log.info("  Attempting to start playback...")

#         for attempt in range(1, 4):
#             if m3u8_event.is_set():
#                 break

#             log.info(f"  ▶ Click attempt {attempt}/3...")

#             # Strategy 1 — direct JS play() call (most reliable)
#             try:
#                 await video_frame.evaluate("""async () => {
#                     const video = document.querySelector('video');
#                     if (video) {
#                         video.muted = true;
#                         try { await video.play(); } catch(e) {}
#                     }
#                 }""")
#                 log.info("    → JS video.play() called")
#             except Exception as e:
#                 log.info(f"    → JS play error: {e}")

#             # Strategy 2 — click the center of the video element
#             try:
#                 coords = await video_frame.evaluate("""() => {
#                     const v = document.querySelector('video');
#                     if (v) {
#                         const r = v.getBoundingClientRect();
#                         return {x: r.left + r.width/2, y: r.top + r.height/2, found: true};
#                     }
#                     return {x: 640, y: 360, found: false};
#                 }""")
#                 await video_frame.mouse.click(coords["x"], coords["y"])
#                 log.info(f"    → Clicked center ({coords['x']:.0f}, {coords['y']:.0f}) "
#                          f"{'[video found]' if coords['found'] else '[fallback pos]'}")
#             except Exception as e:
#                 log.info(f"    → Center click error: {e}")

#             # Strategy 3 — CSS selector clicks (standard players)
#             css_selectors = [
#                 ".jw-icon-display",
#                 ".jw-display-icon-container",
#                 '[aria-label="Play"]',
#                 ".vjs-big-play-button",
#                 ".plyr__control--overlaid",
#                 "video",
#             ]
#             for sel in css_selectors:
#                 try:
#                     el = video_frame.locator(sel).first
#                     if await el.count() > 0:
#                         await el.click(force=True, timeout=2000)
#                         log.info(f"    → CSS selector clicked: {sel}")
#                         break
#                 except Exception:
#                     continue

#             # Strategy 4 — Spacebar
#             try:
#                 await video_frame.keyboard.press("Space")
#                 log.info("    → Spacebar pressed")
#             except Exception:
#                 pass

#             try:
#                 await asyncio.wait_for(m3u8_event.wait(), timeout=5)
#                 break
#             except asyncio.TimeoutError:
#                 await page.screenshot(path=_ss(f"play_attempt_{attempt}"))
#                 continue

#         # ── Final 30s wait ────────────────────────────────────────────────────
#         if not m3u8_event.is_set():
#             log.info("\n  Waiting for m3u8 (up to 30s)...")
#             try:
#                 await asyncio.wait_for(m3u8_event.wait(), timeout=30)
#             except asyncio.TimeoutError:
#                 log.warning("  ⚠ Timeout — m3u8 not captured")
#                 await page.screenshot(path=_ss("ERR_m3u8_timeout"))

#         if m3u8_url:
#             log.info(f"\n✅ Final m3u8 URL:\n{m3u8_url}")
#         else:
#             log.error("❌ m3u8 not found — check screenshots/")

#         # ── Always close the browser ───────────────────────────────────────────
#         await browser.close()
#         log.info("  Browser closed ✓")

#     return m3u8_url


# ══════════════════════════════════════════════════════════════════════════════
# MODE 2 — 480p GDShare URL (working correctly per logs — no changes needed)
# ══════════════════════════════════════════════════════════════════════════════

# async def fetch_gdshare_url(anime_url: str) -> tuple[str | None, int]:
#     """
#     Extract href of last '480p x264' link → navigate download1 page → Gdshare URL.
#     No clicking needed for 480p — just reads the href attribute directly.
#     """
#     gdshare_url = None
#     episode_num = 0

#     async with async_playwright() as p:
#         browser = await p.chromium.launch(
#             headless=Config.HEADLESS,
#             args=["--no-sandbox", "--disable-dev-shm-usage",
#                   "--disable-blink-features=AutomationControlled"],
#         )
#         context = await browser.new_context(user_agent=UA,
#                                              viewport={"width": 1280, "height": 720})
#         await Stealth().apply_stealth_async(context)
#         page = await context.new_page()

#         async def _close_popup(popup):
#             try:
#                 await popup.close()
#             except Exception:
#                 pass
#         page.on("popup", lambda pop: asyncio.create_task(_close_popup(pop)))

#         # ── Load main anime page ──────────────────────────────────────────────
#         log.info(f"[480p] Opening: {anime_url}")
#         await page.goto(anime_url, wait_until="domcontentloaded", timeout=60000)
#         await page.screenshot(path=_ss("480_01_main_page"))
#         log.info(f"  title: {await page.title()}")

#         try:
#             await page.wait_for_selector('a:has-text("480p")', timeout=20000)
#         except Exception:
#             await asyncio.sleep(8)

#         # ── Read 480p hrefs directly (no click = no popup risk) ───────────────
#         hrefs_480: list = await page.eval_on_selector_all(
#             "a",
#             'els => els.filter(e => e.textContent.trim() === "480p x264").map(e => e.href)',
#         )
#         log.info(f"[480p] Found {len(hrefs_480)} 480p x264 links")

#         if not hrefs_480:
#             hrefs_480 = await page.eval_on_selector_all(
#                 "a",
#                 'els => els.filter(e => e.textContent.toLowerCase().includes("480p")).map(e => e.href)',
#             )
#             log.info(f"  Fallback: {len(hrefs_480)} links")

#         if not hrefs_480:
#             await page.screenshot(path=_ss("ERR_no_480p"))
#             log.error("  ❌ No 480p links found")
#             await browser.close()
#             return None, 0

#         episode_num   = len(hrefs_480)
#         download1_url = hrefs_480[-1]
#         log.info(f"  Episode count: {episode_num}")
#         log.info(f"  download1 URL: {download1_url}")

#         # ── Navigate to download1 page ────────────────────────────────────────
#         await page.goto(download1_url, wait_until="domcontentloaded", timeout=60000)
#         await asyncio.sleep(3)
#         await page.screenshot(path=_ss("480_02_download1_page"))
#         log.info(f"  title: {await page.title()}")

#         # ── Find Gdshare <a> (same as original page.py) ───────────────────────
#         gdshare_el = page.locator('a[data-label="Gdshare"]')
#         count = await gdshare_el.count()
#         log.info(f"  Gdshare elements: {count}")

#         if count == 0:
#             await page.screenshot(path=_ss("ERR_no_gdshare"))
#             log.error("  ❌ Gdshare not found")
#             await browser.close()
#             return None, episode_num

#         href = await gdshare_el.first.get_attribute("href") or ""
#         log.info(f"  Gdshare href: {href}")

#         if href.startswith("http") and "gdshare" in href:
#             gdshare_url = href
#         else:
#             try:
#                 async with context.expect_page(timeout=10000) as gp_info:
#                     await gdshare_el.first.click()
#                 gp = await gp_info.value
#                 await asyncio.sleep(2)
#                 gdshare_url = gp.url
#                 await gp.screenshot(path=_ss("480_03_gdshare_tab"))
#                 await gp.close()
#                 log.info(f"  Captured from tab: {gdshare_url}")
#             except Exception as e:
#                 log.warning(f"  Tab capture failed: {e}")

#         log.info(f"[480p] GDShare URL: {gdshare_url}")

#         # ── Always close the browser ───────────────────────────────────────────
#         await browser.close()
#         log.info("  Browser closed ✓")

#     return gdshare_url, episode_num


async def _safe_goto(page, url: str) -> bool:
    """Navigates to a URL and tries to bypass Cloudflare Turnstile by waiting and clicking."""
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    
    # 1. Give Turnstile more time to auto-solve (sometimes takes 8-10s)
    await asyncio.sleep(8) 
    
    for attempt in range(1, 4):
        if not await _has_turnstile(page):
            return True # No Cloudflare detected, we are through!
            
        log.warning(f"  [CF Turnstile] detected. Attempting to solve (Attempt {attempt}/3)...")
        
        # 2. Attempt to physically click the center of the Turnstile widget
        try:
            cf_iframe = page.locator('iframe[src*="challenges.cloudflare.com"]').first
            if await cf_iframe.count() > 0:
                box = await cf_iframe.bounding_box()
                if box:
                    # Move mouse to the center of the iframe and click
                    x = box["x"] + box["width"] / 2
                    y = box["y"] + box["height"] / 2
                    await page.mouse.click(x, y, delay=200) # delay simulates human click duration
                    log.info("  [CF Turnstile] Simulated human click on widget.")
                    await asyncio.sleep(6) # Wait to see if the click solved it
        except Exception:
            pass
            
        # Check if the click worked before we resort to reloading
        if not await _has_turnstile(page):
            return True
            
        log.warning("  [CF Turnstile] Still blocked, reloading page...")
        await page.reload(wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(8)
        
    # Final check after all attempts
    if await _has_turnstile(page):
        log.error("  ❌ Cloudflare block persists after 3 clicks and reloads.")
        return False
        
    return True

# ── Main Fetch Function ───────────────────────────────────────────────────────

async def fetch_gdshare_url(anime_url: str, target_episode: int = None) -> tuple[str | None, int]:
    """
    Extract href of 480p link for a specific episode, ensuring it has a MULTI AUDIO tag.
    If no episode is specified, falls back to the last 480p link.
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
            user_agent=UA,
            viewport={"width": 1280, "height": 720}
        )
        await Stealth().apply_stealth_async(context)
        page = await context.new_page()

        async def _close_popup(popup):
            try:
                await popup.close()
            except Exception:
                pass
        page.on("popup", lambda pop: asyncio.create_task(_close_popup(pop)))

        # ── Load main anime page ──────────────────────────────────────────────
        log.info(f"[480p] Opening: {anime_url}")
        
        # Use our new safe_goto function
        if not await _safe_goto(page, anime_url):
            await browser.close()
            return None, episode_num
            
        log.info(f"  title: {await page.title()}")

        try:
            await page.wait_for_selector('a:has-text("480p")', timeout=20000)
        except Exception:
            await asyncio.sleep(8)

        # ── Read DOM to find the exact Episode + Multi Audio container ────────
        link_data = await page.evaluate(r'''([targetEp]) => {
            const allLinks = Array.from(document.querySelectorAll('a'));
            const p480Links = allLinks.filter(a => a.textContent.trim().toLowerCase().includes("480p"));

            if (p480Links.length === 0) return { href: null, total: 0, msg: "No 480p links found" };
            
            // Fallback if no specific episode is requested
            if (!targetEp) return { href: p480Links[p480Links.length - 1].href, total: p480Links.length, msg: "Fallback to last link" };

            // 1. Regex to handle optional zero padding (e.g. "3" matches "Episode 03")
            const epRegex = new RegExp("Episode\\s*0*" + targetEp + "\\b", "i");

            // Search for exact episode WITH Multi Audio
            for (let a of p480Links) {
                let parent = a.parentElement;
                let foundEp = false;
                let foundMulti = false;

                // Traverse up the DOM tree (up to 6 levels)
                for (let i = 0; i < 6 && parent; i++) {
                    const text = parent.textContent;
                    
                    // FIX: If we climbed too high and hit a container wrapping MULTIPLE episodes, 
                    // stop climbing immediately to avoid grabbing the wrong button.
                    const epCount = (text.match(/Episode\s*\d+/gi) || []).length;
                    if (epCount > 3) break; 

                    if (epRegex.test(text)) foundEp = true;
                    if (/multi\s*audio/i.test(text)) foundMulti = true;

                    if (foundEp && foundMulti) {
                        return { href: a.href, total: p480Links.length, msg: `Found Episode ${targetEp} with Multi Audio` };
                    }
                    parent = parent.parentElement;
                }
            }

            // 2. Fallback: Search for exact episode (ignore missing multi-audio tag)
            for (let a of p480Links) {
                let parent = a.parentElement;
                for (let i = 0; i < 6 && parent; i++) {
                    const text = parent.textContent;
                    
                    // FIX: Prevent over-scoping on the fallback check too
                    const epCount = (text.match(/Episode\s*\d+/gi) || []).length;
                    if (epCount > 3) break;

                    const epRegex = new RegExp("Episode\\s*0*" + targetEp + "\\b", "i");
                    if (epRegex.test(text)) {
                        return { href: a.href, total: p480Links.length, msg: `Found Episode ${targetEp}, but NO Multi-Audio tag found` };
                    }
                    parent = parent.parentElement;
                }
            }

            return { href: null, total: p480Links.length, msg: `Episode ${targetEp} not found` };
        }''', [target_episode])

        download1_url = link_data.get("href")
        episode_num = link_data.get("total", 0)
        
        log.info(f"  DOM Search Result: {link_data.get('msg')}")

        if not download1_url:
            log.error("  ❌ Appropriate 480p link not found")
            await browser.close()
            return None, episode_num

        log.info(f"  download1 URL: {download1_url}")

        # ── Navigate to download1 page ────────────────────────────────────────
        # Use our new safe_goto function here too, just in case download1 throws a captcha
        log.info(f"  Opening download1 URL...")
        if not await _safe_goto(page, download1_url):
            await browser.close()
            return None, episode_num
            
        await asyncio.sleep(3)
        log.info(f"  title: {await page.title()}")

        # ── Find Gdshare <a> ──────────────────────────────────────────────────
        gdshare_el = page.locator('a[data-label="Gdshare"]')
        count = await gdshare_el.count()
        log.info(f"  Gdshare elements: {count}")

        if count == 0:
            log.error("  ❌ Gdshare not found")
            await browser.close()
            return None, episode_num

        href = await gdshare_el.first.get_attribute("href") or ""
        log.info(f"  Gdshare href: {href}")

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
                log.info(f"  Captured from tab: {gdshare_url}")
            except Exception as e:
                log.warning(f"  Tab capture failed: {e}")

        log.info(f"[480p] GDShare URL: {gdshare_url}")
        await browser.close()
        log.info("  Browser closed ✓")

    return gdshare_url, episode_num
