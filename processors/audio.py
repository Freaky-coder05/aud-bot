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


def _get_duration(file_path: Path) -> int:
    """Extract the duration of the audio file in seconds."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries",
             "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)],
            capture_output=True, text=True
        )
        return int(float(r.stdout.strip()))
    except Exception:
        return 0


async def extract_and_convert(mkv_path: Path, output_base: Path) -> tuple[Path | None, int]:
    """
    Extract Tamil audio from `mkv_path` (original stream copy).
    Tries ffprobe first; falls back to common track indices (2, 1, 3).
    Returns a tuple: (Path to audio file, duration_in_seconds), or (None, 0).
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.error("ffmpeg not found")
        return None, 0

    loop = asyncio.get_event_loop()

    # Detect Tamil stream
    stream_idx = await loop.run_in_executor(None, _probe_tamil_index, mkv_path)

    # Build candidate maps: detected first, then common fallbacks
    if stream_idx is not None:
        maps = [f"0:{stream_idx}", "0:a:2", "0:a:1", "0:a:3"]
    else:
        maps = ["0:a:2", "0:a:1", "0:a:3"]   # common Tamil positions

    # Use .mka (Matroska Audio) since we are stream-copying from MKV
    audio_path = output_base.with_suffix(".mka")

    for stream_map in maps:
        log.info(f"  Trying map {stream_map}…")
        cmd = [
            ffmpeg, "-y",
            "-i", str(mkv_path),
            "-map", stream_map,
            "-vn",           # No video
            "-c:a", "copy",  # Copy original audio codec (no re-encoding)
            str(audio_path),
        ]

        def _run(c=cmd):
            return subprocess.run(c, capture_output=True, text=True)

        result = await loop.run_in_executor(None, _run)

        if audio_path.exists() and audio_path.stat().st_size > 0:
            log.info(f"  Audio ready: {audio_path} "
                     f"({audio_path.stat().st_size/1024/1024:.1f} MB)")
            
            # Extract duration before returning
            duration = await loop.run_in_executor(None, _get_duration, audio_path)
            
            return audio_path, duration

        log.warning(f"  map {stream_map} failed — trying next")
        audio_path.unlink(missing_ok=True)

    log.error(f"All stream maps failed. ffmpeg last stderr:\n{result.stderr[-600:]}")
    return None, 0
    
