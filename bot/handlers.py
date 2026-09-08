"""
bot/handlers.py
───────────────
Admin-only Telegram command handlers.

/start          — bot info
/mode           — switch yt-dlp ↔ direct-download (inline buttons)
/add_anime      — add anime to MongoDB
/list_anime     — list all anime
/del_anime      — remove anime
/schedule       — manage weekly/daily schedules (add / list / del)
/run            — manually trigger a download
"""
import logging

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from config import Config
from database import (
    add_anime, add_schedule, get_all_schedules, get_anime,
    get_mode, list_anime, remove_anime, remove_schedule, set_mode,
)

log = logging.getLogger(__name__)

# ── Admin filter ──────────────────────────────────────────────────────────────
_admin = filters.user(Config.ADMIN_IDS)


def register_handlers(bot: Client) -> None:

    # ── /start ────────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("start") & filters.private & _admin)
    async def cmd_start(_c: Client, msg: Message):
        mode      = await get_mode()
        mode_name = "yt-dlp (m3u8)" if mode == 1 else "Direct Download (480p MKV)"
        await msg.reply(
            "🎌 **Anime Tamil Audio Bot**\n\n"
            f"Mode: `{mode_name}`\n\n"
            "**Commands**\n"
            "/mode — switch download mode\n"
            "/add\\_anime — add anime\n"
            "/list\\_anime — list anime\n"
            "/del\\_anime — remove anime\n"
            "/schedule — manage schedules\n"
            "/run — manual download trigger\n",
            disable_web_page_preview=True,
        )

    # ── /mode ─────────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("mode") & filters.private & _admin)
    async def cmd_mode(_c: Client, msg: Message):
        cur = await get_mode()
        await msg.reply(
            f"**Download Mode**\nCurrent: `{'yt-dlp' if cur == 1 else 'Direct'}`\n\n"
            "Pick a mode:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("1️⃣  yt-dlp (m3u8)",     callback_data="mode:1"),
                InlineKeyboardButton("2️⃣  Direct (480p MKV)", callback_data="mode:2"),
            ]]),
        )

    @bot.on_callback_query(filters.regex(r"^mode:[12]$") & _admin)
    async def cb_mode(_c: Client, cq: CallbackQuery):
        mode = int(cq.data.split(":")[1])
        await set_mode(mode)
        label = "yt-dlp (m3u8)" if mode == 1 else "Direct Download (480p MKV)"
        await cq.message.edit_text(f"✅ Mode set to **{label}**")

    # ── /add_anime ────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("add_anime") & filters.private & _admin)
    async def cmd_add_anime(_c: Client, msg: Message):
        """
        /add_anime Name | Season | Page URL [| alias1, alias2]
        """
        parts = msg.text.split(None, 1)
        if len(parts) < 2:
            await msg.reply(
                "**Usage:**\n"
                "`/add_anime Name | Season | URL [| alias1, alias2]`\n\n"
                "**Example:**\n"
                "`/add_anime Skeleton Knight | 1 | https://hindianimeszone.com/... | skeleton knight isekai`"
            )
            return

        args = [x.strip() for x in parts[1].split("|")]
        if len(args) < 3:
            await msg.reply("❌ Need at least: `Name | Season | URL`")
            return

        name    = args[0]
        season  = int(args[1])
        url     = args[2]
        aliases = [a.strip() for a in args[3].split(",")] if len(args) > 3 else []

        await add_anime(name, season, url, aliases)
        alias_str = f"\nAliases: {', '.join(aliases)}" if aliases else ""
        await msg.reply(
            f"✅ **{name}** Season {season} added\n"
            f"`{url}`{alias_str}"
        )

    # ── /list_anime ───────────────────────────────────────────────────────────
    @bot.on_message(filters.command("list_anime") & filters.private & _admin)
    async def cmd_list_anime(_c: Client, msg: Message):
        anime_list = await list_anime()
        if not anime_list:
            await msg.reply("📭 No anime in database yet.")
            return
        lines = []
        for a in anime_list:
            al = f" _(aliases: {', '.join(a['aliases'])})_" if a.get("aliases") else ""
            lines.append(
                f"• **{a['name'].title()}** S{a['season']}{al}\n"
                f"  `{a['page_url']}`"
            )
        await msg.reply("\n\n".join(lines), disable_web_page_preview=True)

    # ── /del_anime ────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("del_anime") & filters.private & _admin)
    async def cmd_del_anime(_c: Client, msg: Message):
        """Usage: /del_anime Name | Season"""
        parts = msg.text.split(None, 1)
        if len(parts) < 2:
            await msg.reply("Usage: `/del_anime Name | Season`")
            return
        args   = [x.strip() for x in parts[1].split("|")]
        name   = args[0]
        season = int(args[1]) if len(args) > 1 else 1
        await remove_anime(name, season)
        await msg.reply(f"✅ Removed: **{name}** S{season}")

    # ── /schedule ─────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("schedule") & filters.private & _admin)
    async def cmd_schedule(_c: Client, msg: Message):
        """
        /schedule add Name | Season | Day | Time(IST 24h)
        /schedule list
        /schedule del Name | Season
        """
        parts = msg.text.split(None, 2)
        if len(parts) < 2:
            await msg.reply(
                "**Schedule usage:**\n"
                "`/schedule add Name | Season | Day | HH:MM`\n"
                "`/schedule list`\n"
                "`/schedule del Name | Season`\n\n"
                "Days: monday…sunday · daily\n"
                "Time: IST 24h  e.g. `18:30`"
            )
            return

        action = parts[1].lower()

        # ── list ──────────────────────────────────────────────────────────────
        if action == "list":
            schedules = await get_all_schedules()
            if not schedules:
                await msg.reply("📭 No schedules set.")
                return
            lines = [
                f"• **{s['anime_name'].title()}** S{s.get('season',1)} — "
                f"{s['day'].capitalize()} {s['time']} IST"
                for s in schedules
            ]
            await msg.reply("\n".join(lines))

        # ── add ───────────────────────────────────────────────────────────────
        elif action == "add" and len(parts) > 2:
            args = [x.strip() for x in parts[2].split("|")]
            if len(args) < 4:
                await msg.reply("Need: `Name | Season | Day | HH:MM`")
                return
            name, season, day, time_str = args[0], int(args[1]), args[2].lower(), args[3]
            await add_schedule(name, season, day, time_str)
            await msg.reply(
                f"✅ Schedule set:\n"
                f"**{name.title()}** S{season} · "
                f"{day.capitalize()} {time_str} IST"
            )

        # ── del ───────────────────────────────────────────────────────────────
        elif action == "del" and len(parts) > 2:
            args   = [x.strip() for x in parts[2].split("|")]
            name   = args[0]
            season = int(args[1]) if len(args) > 1 else 1
            await remove_schedule(name, season)
            await msg.reply(f"✅ Removed schedule: **{name.title()}** S{season}")

        else:
            await msg.reply("Unknown action. Try `/schedule` for help.")

    # ── /run ──────────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("run") & filters.private & _admin)
    async def cmd_run(_c: Client, msg: Message):
        """
        /run Name | Season [| episode_number]
        Manually trigger the full download pipeline.
        """
        parts = msg.text.split(None, 1)
        if len(parts) < 2:
            await msg.reply("Usage: `/run Name | Season [| episode]`")
            return

        args    = [x.strip() for x in parts[1].split("|")]
        name    = args[0]
        season  = int(args[1]) if len(args) > 1 else 1
        episode = int(args[2]) if len(args) > 2 else None

        anime = await get_anime(name, season)
        if not anime:
            await msg.reply(f"❌ Not in database: **{name}** S{season}")
            return

        status = await msg.reply("⏳ Starting…")
        from bot.worker import process_anime_episode
        await process_anime_episode(
            _c, anime,
            episode_override=episode,
            status_msg=status,
        )
