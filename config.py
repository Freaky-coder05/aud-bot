import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Telegram ──────────────────────────────────────────────────────────────
    API_ID       = int(os.getenv("API_ID", 0))
    API_HASH     = os.getenv("API_HASH", "")
    BOT_TOKEN    = os.getenv("BOT_TOKEN", "")
    USER_SESSION = os.getenv("USER_SESSION", "")

    # ── Channels ──────────────────────────────────────────────────────────────
    SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL", "")
    DB_CHANNEL     = int(os.getenv("DB_CHANNEL", 0))
    ADMIN_IDS      = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

    # ── MongoDB (optional) ────────────────────────────────────────────────────
    # Set USE_MONGO=false in .env to use JSON file storage instead
    USE_MONGO = os.getenv("USE_MONGO", "true").lower() == "true"
    MONGO_URI = os.getenv("MONGO_URI", "")
    DB_NAME   = os.getenv("DB_NAME", "anime_audio_bot")

    # JSON fallback path (when USE_MONGO=false)
    JSON_DB_PATH = os.getenv("JSON_DB_PATH", "data/db.json")

    # ── Paths ─────────────────────────────────────────────────────────────────
    DOWNLOAD_DIR   = os.getenv("DOWNLOAD_DIR", "/tmp/anime_dl")
    SCREENSHOT_DIR = os.getenv("SCREENSHOT_DIR", "screenshots")  # Colab debug

    # ── Browser ───────────────────────────────────────────────────────────────
    HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

    # ── Audio ─────────────────────────────────────────────────────────────────
    AUDIO_CODEC   = "libopus"
    AUDIO_BITRATE = "40k"

    # ── Default mode ──────────────────────────────────────────────────────────
    DEFAULT_MODE = int(os.getenv("DEFAULT_MODE", 1))   # 1=ytdlp  2=direct
