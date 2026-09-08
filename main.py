import asyncio
import logging
import os
from pathlib import Path

from aiohttp import web
from pyrogram import Client
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

log = logging.getLogger(__name__)

Path(Config.DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
Path(Config.SCREENSHOT_DIR).mkdir(parents=True, exist_ok=True)


async def health(request):
    return web.Response(text="OK")


async def start_web_server():
    port = int(os.environ.get("PORT", 8000))

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    log.info(f"HTTP server started on 0.0.0.0:{port}")


async def main():

    # ── HTTP server for Koyeb ────────────────────────────────────────────────
    await start_web_server()

    # ── Bot ──────────────────────────────────────────────────────────────────
    bot = Client(
        "bot_session",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        bot_token=Config.BOT_TOKEN,
    )

    from bot.handlers import register_handlers
    register_handlers(bot)

    await bot.start()
    log.info("Bot started ✓")

    # ── Userbot ───────────────────────────────────────────────────────────────
    userbot = None
    session = Config.USER_SESSION.strip()

    if session:
        try:
            userbot = Client(
                "user_session",
                api_id=Config.API_ID,
                api_hash=Config.API_HASH,
                session_string=session,
            )

            from bot.listener import register_listener
            register_listener(userbot, bot)

            await userbot.start()
            log.info("Userbot (channel listener) started ✓")

        except Exception as e:
            log.warning(
                f"Userbot start failed — channel listener disabled.\n"
                f"Reason: {e}"
            )
            userbot = None

    else:
        log.info(
            "USER_SESSION not set — channel listener disabled "
            "(bot-only mode)"
        )

    # ── Scheduler ────────────────────────────────────────────────────────────
    from bot.scheduler import setup_scheduler

    await setup_scheduler(bot)

    log.info("Scheduler started ✓")
    log.info("🚀 Bot is running.")

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
