"""
bot/listener.py
───────────────
Userbot listens to SOURCE_CHANNEL.
For every new post that mentions Tamil audio, parse the anime name and
episode number, look up the anime in MongoDB, and trigger the pipeline.

Expected post format (from the Telegram channel image):
    🔥✨ EPISODE 05 TAMIL & TELUGU ADDED! ✨🔥
    🎬 Title: Skeleton Knight in Another World - Season 1
    🎙 Audio: Hindi-Tamil-Telugu-English-Japanese
    📝 Subtitle: English
    🎥 Dub By: Muse India
    [Watch & Download] [View Schedule] …
"""
import logging
import re

from pyrogram import Client, filters
from pyrogram.types import Message

from config import Config
from database import get_anime, is_episode_done

log = logging.getLogger(__name__)

# ── Regex patterns ────────────────────────────────────────────────────────────
_RE_EP     = re.compile(r"episode\s*(\d+)", re.IGNORECASE)
_RE_TITLE  = re.compile(r"title\s*[:\-]\s*(.+?)(?:\n|$)", re.IGNORECASE)
_RE_SEASON = re.compile(r"season\s*(\d+)", re.IGNORECASE)


def _has_tamil(text: str) -> bool:
    return "tamil" in text.lower()


def _parse_post(text: str) -> tuple[str | None, int, int]:
    """
    Returns (anime_name, season, episode).
    Returns (None, 0, 0) on parse failure.
    """
    title_m  = _RE_TITLE.search(text)
    ep_m     = _RE_EP.search(text)
    season_m = _RE_SEASON.search(text)

    if not title_m:
        return None, 0, 0

    raw_title = title_m.group(1).strip()

    # Strip "- Season N" suffix from title if present (already captured in season_m)
    clean_title = re.sub(r"\s*[-–]\s*season\s*\d+", "", raw_title, flags=re.IGNORECASE).strip()

    season  = int(season_m.group(1)) if season_m else 1
    episode = int(ep_m.group(1))     if ep_m     else 0

    return clean_title, season, episode


def register_listener(userbot: Client, bot: Client) -> None:
    """Attach the channel listener to the userbot."""

    @userbot.on_message(
        filters.chat(Config.SOURCE_CHANNEL) & filters.incoming
    )
    async def _on_post(_ub: Client, msg: Message):
        text = msg.text or msg.caption or ""
        if not text:
            return

        # ── Tamil guard ───────────────────────────────────────────────────────
        if not _has_tamil(text):
            log.debug("[listener] Skipped: no Tamil in post")
            return

        # ── Parse ─────────────────────────────────────────────────────────────
        anime_name, season, episode = _parse_post(text)
        if not anime_name:
            log.debug("[listener] Skipped: could not parse anime name")
            return

        log.info(f"[listener] Tamil post: {anime_name!r} S{season}E{episode:02d}")

        # ── DB lookup ─────────────────────────────────────────────────────────
        anime = await get_anime(anime_name, season)
        if not anime:
            log.warning(f"[listener] Not in DB: {anime_name!r} S{season}")
            # Optionally notify admin
            if Config.ADMIN_IDS:
                try:
                    await bot.send_message(
                        Config.ADMIN_IDS[0],
                        f"⚠️ New post for unknown anime:\n"
                        f"**{anime_name}** S{season}E{episode:02d}\n\n"
                        f"Add it with:\n"
                        f"`/add_anime {anime_name} | {season} | <page_url>`",
                    )
                except Exception:
                    pass
            return

        # ── Duplicate guard ───────────────────────────────────────────────────
        if episode and await is_episode_done(anime["name"], season, episode):
            log.info(f"[listener] Already done: {anime_name} S{season}E{episode:02d}")
            return

        # ── Trigger pipeline ──────────────────────────────────────────────────
        log.info(f"[listener] Starting pipeline: {anime_name} S{season}E{episode:02d}")
        from bot.worker import process_anime_episode
        await process_anime_episode(
            bot,
            anime,
            episode_override=episode,
        )
