import os

from dotenv import load_dotenv

load_dotenv()


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

# Discord
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]

# OAuth2 (웹 대시보드)
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:8080/callback")

# Web
WEB_SECRET_KEY = os.getenv("WEB_SECRET_KEY", "change-me-in-production")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
SESSION_COOKIE_SECURE = _env_flag("SESSION_COOKIE_SECURE", False)
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")

# Database
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/bot.db")

# TTS 보이스 & 언어
VOICES = {
    "M1": "남성 1", "M2": "남성 2", "M3": "남성 3", "M4": "남성 4", "M5": "남성 5",
    "F1": "여성 1", "F2": "여성 2", "F3": "여성 3", "F4": "여성 4", "F5": "여성 5",
}
LANGUAGES = {
    "ko": "한국어", "en": "English", "es": "Español", "pt": "Português", "fr": "Français",
}

DEFAULT_USER_SETTINGS = {
    "voice": "M1",
    "speed": 1.0,
    "lang": "ko",
    "total_steps": 2,
}
