"""
gd_downloader.py
────────────────
Full gdshare.top download flow:
  Generate Download Links → Instant Download (ad popup) → Download Here → file
"""
import asyncio
import logging
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PwTimeout
from config import Config

log = logging.getLogger(__name__)


async def _wait_text(page, text: str, timeout: int = 30_000):
    loc = page.get_by_text(text, exact=False).first
    await loc.wait_for(state="visible", timeout=timeout)
    return loc


async def _click_close_popup(page, ctx, locator):
    """Click `locator`; if a new tab opens close it, then return."""
    try:
        async with ctx.expect_page(timeout=10_000) as pi:
            await locator.click()
        popup = await pi.value
        log.info(f"  [popup] {popup.url[:60]}")
        try:
            await popup.wait_for_load_state("domcontentloaded", timeout=5000)
        except PwTimeout:
            pass
        if not popup.is_closed():
            await popup.close()
        log.info("  [popup] closed")
    except PwTimeout:
        log.info("  [popup] none")


async def download_from_gdshare(gdshare_url: str,
                                 download_dir: Path) -> Path | None:
    """
    Navigate the gdshare.top flow and download the file.
    Returns the local Path to the downloaded file, or None.
    """
    download_dir.mkdir(parents=True, exist_ok=True)
    result_path = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=Config.HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(accept_downloads=True)
        page = await ctx.new_page()

        log.info(f"[gdshare] Opening: {gdshare_url}")
        await page.goto(gdshare_url, wait_until="domcontentloaded", timeout=60_000)

        # ── 1. Generate Download Links ────────────────────────────────────────
        log.info("[gdshare] Waiting for Generate Download Links…")
        gen_btn = await _wait_text(page, "Generate Download Links", 30_000)
        log.info("[gdshare] Clicking Generate Download Links…")
        await _click_close_popup(page, ctx, gen_btn)

        # ── 2. Instant Download (first click = ad popup) ──────────────────────
        log.info("[gdshare] Waiting for Instant Download…")
        instant = await _wait_text(page, "Instant Download", 60_000)
        log.info("[gdshare] First click (ad expected)…")
        await _click_close_popup(page, ctx, instant)

        # ── 3. Check page state ───────────────────────────────────────────────
        dl_here  = page.get_by_text("Download Here",    exact=False).first
        instant2 = page.get_by_text("Instant Download", exact=False).first

        if await dl_here.is_visible():
            log.info("[gdshare] Download Here already visible")
        elif await instant2.is_visible():
            log.info("[gdshare] Second Instant Download click…")
            await instant2.click()
        else:
            log.error("[gdshare] Neither button visible — aborting")
            await browser.close()
            return None

        # ── 4. Download Here ──────────────────────────────────────────────────
        log.info("[gdshare] Waiting for Download Here…")
        dl_btn = await _wait_text(page, "Download Here", 60_000)
        log.info("[gdshare] Downloading…")

        try:
            async with page.expect_download(timeout=120_000) as dl_info:
                await dl_btn.click()
            download = await dl_info.value
            save_path = download_dir / download.suggested_filename
            await download.save_as(str(save_path))
            log.info(f"[gdshare] Saved: {save_path} "
                     f"({save_path.stat().st_size/1024/1024:.1f} MB)")
            result_path = save_path
        except PwTimeout:
            log.error("[gdshare] Download event not triggered")

        await browser.close()

    return result_path
