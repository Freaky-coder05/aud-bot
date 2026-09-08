"""
m3u8_downloader.py
──────────────────
1. Use yt-dlp to find the Tamil (ta) audio playlist URL inside a master.m3u8
2. Download each TS segment (stripping fake PNG headers)
3. Convert assembled .ts → .opus via ffmpeg
"""
import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urljoin

import aiohttp
import yt_dlp

from config import Config

log = logging.getLogger(__name__)

# Fake-PNG IEND sentinel; real audio bytes start right after
_IEND = b'\x00\x00\x00\x00IEND\xaeB`\x82'

_HEADERS = {
    "Accept":          "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://hindianimezone.p2pplay.online/",
    "User-Agent":      ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:154.0) "
                        "Gecko/20100101 Firefox/154.0"),
}


def _strip_png(data: bytes) -> bytes:
    idx = data.find(_IEND)
    return data[idx + 12:] if idx != -1 else data


def _find_tamil_playlist(m3u8_url: str) -> str | None:
    """Blocking yt-dlp call — run in executor."""
    with yt_dlp.YoutubeDL({
        "format":       "bestaudio[language=ta]",
        "http_headers": _HEADERS,
        "quiet":        True,
    }) as ydl:
        info = ydl.extract_info(m3u8_url, download=False)

    selected = next(
        (f for f in info.get("formats", []) if f.get("language") == "ta"),
        None,
    )
    if not selected:
        return None
    return selected.get("url") or selected.get("manifest_url")


async def download_tamil_audio(m3u8_url: str, output_base: Path) -> Path | None:
    """
    Download Tamil audio track from m3u8.
    Returns Path to .opus file, or None on failure.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.error("ffmpeg not found in PATH")
        return None

    loop = asyncio.get_event_loop()

    # ── Step 1: Tamil playlist URL ────────────────────────────────────────────
    log.info("[m3u8dl] Finding Tamil playlist…")
    playlist_url = await loop.run_in_executor(None, _find_tamil_playlist, m3u8_url)
    if not playlist_url:
        log.error("[m3u8dl] Tamil (ta) format not found in m3u8")
        return None
    log.info("[m3u8dl] Tamil playlist found ✓")

    # ── Step 2: Segment list ──────────────────────────────────────────────────
    async with aiohttp.ClientSession(headers=_HEADERS) as session:
        async with session.get(playlist_url) as resp:
            playlist_text = await resp.text()

    seg_urls = [
        urljoin(playlist_url, line.strip())
        for line in playlist_text.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    log.info(f"[m3u8dl] Segments to download: {len(seg_urls)}")

    # ── Step 3: Download segments ─────────────────────────────────────────────
    raw_ts = output_base.with_suffix(".ts")
    async with aiohttp.ClientSession(headers=_HEADERS) as session:
        with open(raw_ts, "wb") as out:
            for i, url in enumerate(seg_urls, 1):
                try:
                    async with session.get(url) as resp:
                        raw = await resp.read()
                    out.write(_strip_png(raw))
                except Exception as e:
                    log.warning(f"  Segment {i} error: {e}")
                if i % 30 == 0 or i == len(seg_urls):
                    log.info(f"  {i}/{len(seg_urls)}")

    log.info(f"[m3u8dl] Raw .ts: {raw_ts.stat().st_size/1024/1024:.1f} MB")

    # ── Step 4: Convert to libopus ────────────────────────────────────────────
    opus_path = output_base.with_suffix(".opus")
    log.info("[m3u8dl] Converting to libopus…")

    def _convert():
        return subprocess.run(
            [ffmpeg, "-y", "-i", str(raw_ts),
             "-vn", "-c:a", Config.AUDIO_CODEC, "-b:a", Config.AUDIO_BITRATE,
             str(opus_path)],
            capture_output=True, text=True,
        )

    result = await loop.run_in_executor(None, _convert)
    raw_ts.unlink(missing_ok=True)

    if opus_path.exists() and opus_path.stat().st_size > 0:
        log.info(f"[m3u8dl] Done: {opus_path} "
                 f"({opus_path.stat().st_size/1024/1024:.1f} MB)")
        return opus_path

    log.error(f"[m3u8dl] ffmpeg failed:\n{result.stderr[-800:]}")
    return None
