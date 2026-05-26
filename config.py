import os

from dotenv import load_dotenv

load_dotenv()


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int_set(name: str) -> set[int]:
    value = os.getenv(name, "")
    items = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        items.add(int(part))
    return items

# Discord
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]

# OAuth2 (웹 대시보드)
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:5001/callback")
DASHBOARD_ADMIN_IDS = _env_int_set("DASHBOARD_ADMIN_IDS")

# Web
WEB_SECRET_KEY = os.getenv("WEB_SECRET_KEY", "change-me-in-production")
WEB_PORT = int(os.getenv("WEB_PORT", "5001"))
SESSION_COOKIE_SECURE = _env_flag("SESSION_COOKIE_SECURE", False)
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")

# Database
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/bot.db")
DAILY_STATS_RETENTION_DAYS = int(os.getenv("DAILY_STATS_RETENTION_DAYS", "365"))

# Logging
LOG_PATH = os.getenv("LOG_PATH", "logs/app.log")
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "30"))

# TTS 엔진
TTS_ENGINES = {
    "supertonic": "Supertonic-3",
    "google": "Google TTS",
}

# Supertonic 보이스
SUPERTONIC_VOICES = {
    "M1": "남성 1", "M2": "남성 2", "M3": "남성 3", "M4": "남성 4", "M5": "남성 5",
    "F1": "여성 1", "F2": "여성 2", "F3": "여성 3", "F4": "여성 4", "F5": "여성 5",
}

# Google TTS
GOOGLE_TTS_API_KEY = os.getenv("GOOGLE_TTS_API_KEY", "")

# Google TTS 보이스 (Standard만 사용, 월 400만자 무료)
GOOGLE_VOICES = {
    "ko-KR-Standard-A": "여성 A",
    "ko-KR-Standard-B": "남성 B",
}

GOOGLE_TTS_FREE_LIMIT = 4_000_000

# 하위 호환용 (VOICES = 현재 기본 엔진의 보이스)
VOICES = SUPERTONIC_VOICES

# Supertonic 3는 31개 언어 + 자동 감지(na) 지원
LANGUAGES = {
    "na": "자동 감지",
    "ko": "한국어", "en": "English", "ja": "日本語",
    "es": "Español", "pt": "Português", "fr": "Français", "de": "Deutsch",
    "it": "Italiano", "nl": "Nederlands", "ru": "Русский", "uk": "Українська",
    "pl": "Polski", "cs": "Čeština", "sk": "Slovenčina", "hu": "Magyar",
    "ro": "Română", "bg": "Български", "hr": "Hrvatski", "sl": "Slovenščina",
    "sv": "Svenska", "da": "Dansk", "fi": "Suomi", "et": "Eesti",
    "lv": "Latviešu", "lt": "Lietuvių", "el": "Ελληνικά", "tr": "Türkçe",
    "ar": "العربية", "hi": "हिन्दी", "id": "Bahasa Indonesia", "vi": "Tiếng Việt",
}

# Supertonic 3 권장 추론 스텝 범위 (5~12, 기본 8)
SUPERTONIC_DEFAULT_STEPS = 8
SUPERTONIC_MIN_STEPS = 5
SUPERTONIC_MAX_STEPS = 12

DEFAULT_USER_SETTINGS = {
    "engine": "google",
    "voice": "ko-KR-Standard-A",
    "speed": 1.0,
    "lang": "ko",
    "total_steps": SUPERTONIC_DEFAULT_STEPS,
}
