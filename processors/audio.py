"""
processors/audio.py
───────────────────
Extract Tamil audio track from MKV, convert to libopus 40 kbps .opus file.
"""
import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path

from config import Config

log = logging.getLogger(__name__)

# Languages that map to Tamil
_TAMIL_LANGS  = {"tam", "ta"}
_TAMIL_TITLES = {"tamil", "ta", "tam"}


def _probe_tamil_index(mkv: Path) -> int | None:
    """Return ffmpeg stream index for the Tamil audio track, or None."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None

    r = subprocess.run(
        [ffprobe, "-v", "quiet", "-print_format", "json",
         "-show_streams", str(mkv)],
        capture_output=True, text=True,
    )
    try:
        streams = json.loads(r.stdout).get("streams", [])
    except Exception:
        return None

    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        tags  = s.get("tags", {})
        lang  = (tags.get("language") or tags.get("LANGUAGE") or "").lower()
        title = (tags.get("title")    or tags.get("TITLE")    or "").lower()
        if lang in _TAMIL_LANGS or any(t in title for t in _TAMIL_TITLES):
            log.info(f"  Tamil stream index: {s['index']} (lang={lang}, title={title})")
            return s["index"]

    log.warning("  Tamil stream not found via ffprobe tags")
    return None


async def extract_and_convert(mkv_path: Path, output_base: Path) -> Path | None:
    """
    Extract Tamil audio from `mkv_path`, write `output_base.opus`.
    Tries ffprobe first; falls back to common track indices (2, 1, 3).
    Returns Path to .opus, or None.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.error("ffmpeg not found")
        return None

    loop = asyncio.get_event_loop()

    # Detect Tamil stream
    stream_idx = await loop.run_in_executor(None, _probe_tamil_index, mkv_path)

    # Build candidate maps: detected first, then common fallbacks
    if stream_idx is not None:
        maps = [f"0:{stream_idx}", "0:a:2", "0:a:1", "0:a:3"]
    else:
        maps = ["0:a:2", "0:a:1", "0:a:3"]   # common Tamil positions

    opus_path = output_base.with_suffix(".opus")

    for stream_map in maps:
        log.info(f"  Trying map {stream_map}…")
        cmd = [
            ffmpeg, "-y",
            "-i", str(mkv_path),
            "-map", stream_map,
            "-vn",
            "-c:a", Config.AUDIO_CODEC,
            "-b:a", Config.AUDIO_BITRATE,
            str(opus_path),
        ]

        def _run(c=cmd):
            return subprocess.run(c, capture_output=True, text=True)

        result = await loop.run_in_executor(None, _run)

        if opus_path.exists() and opus_path.stat().st_size > 0:
            log.info(f"  Opus ready: {opus_path} "
                     f"({opus_path.stat().st_size/1024/1024:.1f} MB)")
            return opus_path

        log.warning(f"  map {stream_map} failed — trying next")
        opus_path.unlink(missing_ok=True)

    log.error(f"All stream maps failed. ffmpeg last stderr:\n{result.stderr[-600:]}")
    return None
