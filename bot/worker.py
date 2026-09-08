"""
bot/worker.py
─────────────
Full pipeline: scrape → download → extract Tamil audio → upload to DB channel.
Called by /run command, scheduler, and channel listener.
"""
import logging
import shutil
from pathlib import Path

from config import Config
from database import get_mode, is_episode_done, mark_episode_done

log = logging.getLogger(__name__)


async def _status(msg, text: str):
    log.info(text)
    if msg:
        try:
            await msg.edit_text(text)
        except Exception:
            pass


async def _upload(client, opus: Path,
                  anime_name: str, season: int, episode: int | None):
    ep_tag = f"E{episode:02d}" if episode else "Latest"
    caption = (
        f"🎌 **{anime_name.title()}**\n"
        f"Season {season} · {ep_tag}\n"
        f"🎧 Tamil Audio · libopus {Config.AUDIO_BITRATE}"
    )
    try:
        await client.send_audio(
            chat_id   = Config.DB_CHANNEL,
            audio     = str(opus),
            caption   = caption,
            title     = f"{anime_name.title()} S{season:02d}{ep_tag} [TA]",
            performer = "Anime Audio Bot",
        )
        log.info(f"Uploaded: {opus.name}")
        if episode:
            mode = await get_mode()
            await mark_episode_done(anime_name, season, episode, mode)
    except Exception as e:
        log.error(f"Upload failed: {e}")


async def process_anime_episode(
    client,
    anime: dict,
    episode_override: int | None = None,
    status_msg=None,
):
    """
    Full pipeline for one anime entry.

    anime dict must have: name, season, page_url
    """
    mode      = await get_mode()
    name      = anime["name"]
    season    = anime.get("season", 1)
    page_url  = anime["page_url"]

    dl_dir = Path(Config.DOWNLOAD_DIR) / name.replace(" ", "_")
    dl_dir.mkdir(parents=True, exist_ok=True)

    # ── Duplicate guard ───────────────────────────────────────────────────────
    if episode_override and await is_episode_done(name, season, episode_override):
        await _status(status_msg,
                      f"⏭ Already done: {name.title()} S{season}E{episode_override:02d}")
        return

    try:
        if mode == 1:
            await _pipeline_ytdlp(
                client, name, season, page_url,
                dl_dir, episode_override, status_msg,
            )
        else:
            await _pipeline_direct(
                client, name, season, page_url,
                dl_dir, episode_override, status_msg,
            )
    finally:
        try:
            shutil.rmtree(dl_dir, ignore_errors=True)
        except Exception:
            pass


# ── Mode 1 — yt-dlp / m3u8 ───────────────────────────────────────────────────

async def _pipeline_ytdlp(client, name, season, page_url,
                           dl_dir, episode, status_msg):
    from scrapers.episode_fetcher import fetch_m3u8
    from scrapers.m3u8_downloader import download_tamil_audio

    await _status(status_msg, "🔍 Fetching m3u8 URL…")
    m3u8 = await fetch_m3u8(page_url)
    if not m3u8:
        await _status(status_msg, "❌ Could not capture m3u8 URL")
        return

    await _status(status_msg, "⬇️ Downloading Tamil audio segments…")
    base   = dl_dir / f"{name}_S{season:02d}"
    opus   = await download_tamil_audio(m3u8, base)
    if not opus:
        await _status(status_msg, "❌ Audio download/conversion failed")
        return

    await _status(status_msg, "📤 Uploading to channel…")
    await _upload(client, opus, name, season, episode)
    opus.unlink(missing_ok=True)
    await _status(status_msg, f"✅ Done: {name.title()} S{season}")


# ── Mode 2 — Direct 480p MKV ─────────────────────────────────────────────────

async def _pipeline_direct(client, name, season, page_url,
                            dl_dir, episode, status_msg):
    from scrapers.episode_fetcher  import fetch_gdshare_url
    from scrapers.gd_downloader    import download_from_gdshare
    from processors.audio          import extract_and_convert

    await _status(status_msg, "🔍 Finding 480p GDShare link…")
    gdshare_url, detected_ep = await fetch_gdshare_url(page_url, target_episode=episode)

    if not gdshare_url:
        await _status(status_msg, "❌ GDShare URL not found")
        return

    # Use detected episode number if none was given
    ep = episode or detected_ep

    # Duplicate guard for detected episode
    if ep and await is_episode_done(name, season, ep):
        await _status(status_msg,
                      f"⏭ Already done: {name.title()} S{season}E{ep:02d}")
        return

    await _status(status_msg, "⬇️ Downloading 480p MKV…")
    mkv = await download_from_gdshare(gdshare_url, dl_dir)
    if not mkv:
        await _status(status_msg, "❌ MKV download failed")
        return

    await _status(status_msg, "🎵 Extracting Tamil audio track…")
    base = dl_dir / f"{name}_S{season:02d}_ta"
    opus = await extract_and_convert(mkv, base)
    mkv.unlink(missing_ok=True)

    if not opus:
        await _status(status_msg, "❌ Tamil audio extraction failed")
        return

    await _status(status_msg, "📤 Uploading to channel…")
    await _upload(client, opus, name, season, ep)
    opus.unlink(missing_ok=True)
    await _status(status_msg, f"✅ Done: {name.title()} S{season}")
