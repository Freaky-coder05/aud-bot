"""
bot/scheduler.py
────────────────
Reads schedules from MongoDB and registers APScheduler cron jobs (IST).
Call setup_scheduler() on startup; call refresh_scheduler() after
adding/removing schedules.
"""
import logging

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from database import get_all_schedules, get_anime

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

_DOW = {
    "monday": "mon", "tuesday": "tue", "wednesday": "wed",
    "thursday": "thu", "friday": "fri", "saturday": "sat",
    "sunday": "sun", "daily": "*",
}


async def _trigger_download(bot, anime_name: str, season: int):
    anime = await get_anime(anime_name, season)
    if not anime:
        log.warning(f"[scheduler] Anime not in DB: {anime_name} S{season}")
        return
    log.info(f"[scheduler] Auto-run: {anime_name} S{season}")
    from bot.worker import process_anime_episode
    await process_anime_episode(bot, anime)


def _add_jobs(scheduler: AsyncIOScheduler, schedules: list[dict], bot) -> None:
    for s in schedules:
        name     = s["anime_name"]
        season   = s.get("season", 1)
        day      = s.get("day", "daily")
        time_str = s.get("time", "12:00")

        h, m     = map(int, time_str.split(":"))
        dow      = _DOW.get(day, "*")

        scheduler.add_job(
            _trigger_download,
            CronTrigger(day_of_week=dow, hour=h, minute=m, timezone=IST),
            args=[bot, name, season],
            id=f"{name.lower()}_{season}",
            replace_existing=True,
        )
        log.info(f"[scheduler] Registered: {name} S{season} — {day} {time_str} IST")


async def setup_scheduler(bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=IST)
    schedules = await get_all_schedules()
    _add_jobs(scheduler, schedules, bot)
    scheduler.start()
    log.info(f"[scheduler] Started with {len(schedules)} job(s)")
    return scheduler


async def refresh_scheduler(scheduler: AsyncIOScheduler, bot) -> None:
    """Call after /schedule add or /schedule del to reload jobs."""
    scheduler.remove_all_jobs()
    schedules = await get_all_schedules()
    _add_jobs(scheduler, schedules, bot)
    log.info(f"[scheduler] Refreshed: {len(schedules)} job(s)")
