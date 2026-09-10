import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Telegram ──────────────────────────────────────────────────────────────
    API_ID       = int(os.getenv("API_ID", 21740783))
    API_HASH     = os.getenv("API_HASH", "a5dc7fec8302615f5b441ec5e238cd46")
    BOT_TOKEN    = os.getenv("BOT_TOKEN", "7722665729:AAEVKhEE8bwXnF6P0j1aGCVeBwgkypfS_OM")
    USER_SESSION = os.getenv("USER_SESSION", "false")
    PROXY_URL = ""

    # ── Channels ──────────────────────────────────────────────────────────────
    SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL", "@HindiAnimeZonecom")
    DB_CHANNEL     = int(os.getenv("DB_CHANNEL", -1002201298270))
    ADMIN_IDS      = [int(x) for x in os.getenv("ADMIN_IDS", "6299192020,6693549185").split(",") if x.strip()]

    # ── MongoDB (optional) ────────────────────────────────────────────────────
    # Set USE_MONGO=false in .env to use JSON file storage instead
    USE_MONGO = os.getenv("USE_MONGO", "true").lower() == "true"
    MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://Speedwolf1:speedwolf24689@cluster0.rgfywsf.mongodb.net/")
    DB_NAME   = os.getenv("DB_NAME", "Speedwolf1")

    # JSON fallback path (when USE_MONGO=false)
    JSON_DB_PATH = os.getenv("JSON_DB_PATH", "data/db.json")

    # ── Paths ─────────────────────────────────────────────────────────────────
    DOWNLOAD_DIR   = os.getenv("DOWNLOAD_DIR", "/tmp/anime_dl")
    SCREENSHOT_DIR = os.getenv("SCREENSHOT_DIR", "screenshots")  # Colab debug

    # ── Browser ───────────────────────────────────────────────────────────────
    HEADLESS = os.getenv("HEADLESS", "false").lower() == "false"

    # ── Audio ─────────────────────────────────────────────────────────────────
    AUDIO_CODEC   = "libopus"
    AUDIO_BITRATE = "40k"

    # ── Default mode ──────────────────────────────────────────────────────────
    DEFAULT_MODE = int(os.getenv("DEFAULT_MODE", 2))   # 1=ytdlp  2=direct
