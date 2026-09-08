"""
main.py — entry point
Starts: Bot client + (optional) Userbot + APScheduler
"""
import asyncio
import logging
from pathlib import Path

from pyrogram import Client
from config import Config

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

Path(Config.DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
Path(Config.SCREENSHOT_DIR).mkdir(parents=True, exist_ok=True)


async def main() -> None:

    # ── Bot ───────────────────────────────────────────────────────────────────
    bot = Client(
        "bot_session",
        api_id    = Config.API_ID,
        api_hash  = Config.API_HASH,
        bot_token = Config.BOT_TOKEN,
    )

    from bot.handlers import register_handlers
    register_handlers(bot)

    await bot.start()
    log.info("Bot started ✓")

    # ── Userbot (channel listener) — OPTIONAL ─────────────────────────────────
    # Only starts when USER_SESSION is a valid Pyrogram session string.
    # Leave USER_SESSION blank in .env to skip this entirely.
    userbot = None
    session = Config.USER_SESSION.strip()

    if session:
        try:
            userbot = Client(
                "user_session",
                api_id         = Config.API_ID,
                api_hash       = Config.API_HASH,
                session_string = session,
            )
            from bot.listener import register_listener
            register_listener(userbot, bot)
            await userbot.start()
            log.info("Userbot (channel listener) started ✓")
        except Exception as e:
            log.warning(
                f"Userbot start failed — channel listener disabled.\n"
                f"  Reason: {e}\n"
                f"  Fix: generate a fresh session string and set USER_SESSION in .env\n"
                f"  Run:  python gen_session.py"
            )
            userbot = None
    else:
        log.info("USER_SESSION not set — channel listener disabled (bot-only mode)")

    # ── Scheduler ─────────────────────────────────────────────────────────────
    from bot.scheduler import setup_scheduler
    await setup_scheduler(bot)
    log.info("Scheduler started ✓")

    log.info("🚀 Bot is running. Use /start in Telegram.")
    await asyncio.Event().wait()   # run forever


if __name__ == "__main__":
    asyncio.run(main())
