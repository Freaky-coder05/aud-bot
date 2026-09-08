"""
database.py
───────────
Unified storage layer — MongoDB OR local JSON file.

Set  USE_MONGO=true  in .env  → uses MongoDB Atlas (motor)
Set  USE_MONGO=false in .env  → uses  data/db.json  (no external dependency)

Both backends expose exactly the same async API so the rest of the code
never needs to know which one is active.
"""
import asyncio
import json
import logging
from pathlib import Path

from config import Config

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# JSON BACKEND
# ═══════════════════════════════════════════════════════════════════════════════

_DB_FILE = Path(Config.JSON_DB_PATH)
_LOCK    = asyncio.Lock()

_EMPTY_DB = {
    "anime":    [],
    "schedules": [],
    "settings": {"mode": Config.DEFAULT_MODE},
    "episodes": [],
}


def _read_json() -> dict:
    _DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _DB_FILE.exists():
        _write_json(_EMPTY_DB)
        return _EMPTY_DB.copy()
    with open(_DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(data: dict) -> None:
    _DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── JSON helpers (run blocking I/O in executor) ───────────────────────────────

async def _jread() -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_json)


async def _jwrite(data: dict) -> None:
    loop = asyncio.get_event_loop()
    async with _LOCK:
        await loop.run_in_executor(None, _write_json, data)


# ═══════════════════════════════════════════════════════════════════════════════
# MONGO BACKEND (imported only when USE_MONGO=true)
# ═══════════════════════════════════════════════════════════════════════════════

if Config.USE_MONGO:
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        _mclient     = AsyncIOMotorClient(Config.MONGO_URI)
        _mdb         = _mclient[Config.DB_NAME]
        _anime_col   = _mdb["anime_dict"]
        _sched_col   = _mdb["schedules"]
        _set_col     = _mdb["settings"]
        _ep_col      = _mdb["processed_eps"]
        log.info("MongoDB backend active")
    except Exception as e:
        log.warning(f"MongoDB import/connect failed ({e}) — falling back to JSON")
        Config.USE_MONGO = False


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API — same signatures regardless of backend
# ═══════════════════════════════════════════════════════════════════════════════

# ── Anime dictionary ──────────────────────────────────────────────────────────

async def add_anime(name: str, season: int, page_url: str,
                    aliases: list | None = None) -> None:
    doc = {
        "name":     name.lower(),
        "season":   season,
        "page_url": page_url,
        "aliases":  [a.lower() for a in (aliases or [])],
    }
    if Config.USE_MONGO:
        await _anime_col.update_one(
            {"name": name.lower(), "season": season},
            {"$set": doc}, upsert=True,
        )
    else:
        db = await _jread()
        db["anime"] = [a for a in db["anime"]
                       if not (a["name"] == name.lower() and a["season"] == season)]
        db["anime"].append(doc)
        await _jwrite(db)
    log.info(f"Anime saved: {name} S{season}")


async def get_anime(name: str, season: int | None = None) -> dict | None:
    q = name.lower()
    if Config.USE_MONGO:
        query: dict = {"$or": [{"name": q}, {"aliases": q}]}
        if season:
            query["season"] = season
        return await _anime_col.find_one(query)
    else:
        db = await _jread()
        for a in db["anime"]:
            name_ok   = a["name"] == q or q in a.get("aliases", [])
            season_ok = (season is None) or (a["season"] == season)
            if name_ok and season_ok:
                return a
        return None


async def list_anime() -> list:
    if Config.USE_MONGO:
        return await _anime_col.find().to_list(None)
    db = await _jread()
    return db["anime"]


async def remove_anime(name: str, season: int) -> None:
    if Config.USE_MONGO:
        await _anime_col.delete_one({"name": name.lower(), "season": season})
    else:
        db = await _jread()
        db["anime"] = [a for a in db["anime"]
                       if not (a["name"] == name.lower() and a["season"] == season)]
        await _jwrite(db)


# ── Settings ─────────────────────────────────────────────────────────────────

async def get_mode() -> int:
    if Config.USE_MONGO:
        doc = await _set_col.find_one({"key": "mode"})
        return int(doc["value"]) if doc else Config.DEFAULT_MODE
    db = await _jread()
    return db.get("settings", {}).get("mode", Config.DEFAULT_MODE)


async def set_mode(mode: int) -> None:
    if Config.USE_MONGO:
        await _set_col.update_one(
            {"key": "mode"}, {"$set": {"value": mode}}, upsert=True,
        )
    else:
        db = await _jread()
        db.setdefault("settings", {})["mode"] = mode
        await _jwrite(db)


# ── Schedules ─────────────────────────────────────────────────────────────────

async def add_schedule(anime_name: str, season: int,
                       day: str, time_str: str) -> None:
    doc = {
        "anime_name": anime_name.lower(),
        "season":     season,
        "day":        day.lower(),
        "time":       time_str,
        "active":     True,
    }
    if Config.USE_MONGO:
        await _sched_col.update_one(
            {"anime_name": anime_name.lower(), "season": season},
            {"$set": doc}, upsert=True,
        )
    else:
        db = await _jread()
        db["schedules"] = [s for s in db["schedules"]
                           if not (s["anime_name"] == anime_name.lower()
                                   and s["season"] == season)]
        db["schedules"].append(doc)
        await _jwrite(db)


async def get_all_schedules() -> list:
    if Config.USE_MONGO:
        return await _sched_col.find({"active": True}).to_list(None)
    db = await _jread()
    return [s for s in db.get("schedules", []) if s.get("active")]


async def remove_schedule(anime_name: str, season: int) -> None:
    if Config.USE_MONGO:
        await _sched_col.delete_one({"anime_name": anime_name.lower(), "season": season})
    else:
        db = await _jread()
        db["schedules"] = [s for s in db["schedules"]
                           if not (s["anime_name"] == anime_name.lower()
                                   and s["season"] == season)]
        await _jwrite(db)


# ── Processed episodes ────────────────────────────────────────────────────────

async def is_episode_done(anime_name: str, season: int, episode: int) -> bool:
    if Config.USE_MONGO:
        doc = await _ep_col.find_one({
            "anime": anime_name.lower(), "season": season,
            "episode": episode, "done": True,
        })
        return doc is not None
    db = await _jread()
    return any(
        e["anime"] == anime_name.lower() and e["season"] == season
        and e["episode"] == episode and e.get("done")
        for e in db.get("episodes", [])
    )


async def mark_episode_done(anime_name: str, season: int,
                             episode: int, mode: int) -> None:
    if Config.USE_MONGO:
        await _ep_col.update_one(
            {"anime": anime_name.lower(), "season": season, "episode": episode},
            {"$set": {"mode": mode, "done": True}}, upsert=True,
        )
    else:
        db = await _jread()
        db["episodes"] = [e for e in db.get("episodes", [])
                          if not (e["anime"] == anime_name.lower()
                                  and e["season"] == season
                                  and e["episode"] == episode)]
        db["episodes"].append({
            "anime":   anime_name.lower(),
            "season":  season,
            "episode": episode,
            "mode":    mode,
            "done":    True,
        })
        await _jwrite(db)


async def get_last_done_episode(anime_name: str, season: int) -> int:
    if Config.USE_MONGO:
        doc = await _ep_col.find_one(
            {"anime": anime_name.lower(), "season": season, "done": True},
            sort=[("episode", -1)],
        )
        return doc["episode"] if doc else 0
    db = await _jread()
    done = [
        e["episode"] for e in db.get("episodes", [])
        if e["anime"] == anime_name.lower()
        and e["season"] == season and e.get("done")
    ]
    return max(done, default=0)
