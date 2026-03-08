import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite
from loguru import logger as log

from config import DAILY_STATS_RETENTION_DAYS, DATABASE_PATH, DEFAULT_USER_SETTINGS

_db: aiosqlite.Connection | None = None

# on_message 성능을 위한 메모리 캐시
_tts_channels_cache: dict[int, list[int]] = {}
KST = ZoneInfo("Asia/Seoul")


def _day_key(target_day: date | None = None) -> str:
    if target_day is None:
        target_day = datetime.now(KST).date()
    return target_day.isoformat()


async def init_db():
    global _db
    db_path = Path(DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    _db = await aiosqlite.connect(str(db_path))
    await _db.execute("PRAGMA journal_mode=WAL")

    await _db.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            voice TEXT DEFAULT 'M1',
            speed REAL DEFAULT 1.0,
            lang TEXT DEFAULT 'ko',
            total_steps INTEGER DEFAULT 2
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS tts_channels (
            guild_id INTEGER,
            channel_id INTEGER,
            PRIMARY KEY (guild_id, channel_id)
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            day TEXT PRIMARY KEY,
            tts_requests INTEGER DEFAULT 0,
            guild_count INTEGER DEFAULT 0,
            active_channel_count INTEGER DEFAULT 0
        )
    """)
    await _db.commit()
    await purge_old_daily_stats()

    # 캐시 워밍업
    await _refresh_cache()

    # JSON 마이그레이션
    await _migrate_from_json()

    log.info("데이터베이스 초기화 완료")


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None


async def _refresh_cache():
    global _tts_channels_cache
    _tts_channels_cache = {}
    async with _db.execute("SELECT guild_id, channel_id FROM tts_channels") as cursor:
        async for row in cursor:
            _tts_channels_cache.setdefault(row[0], []).append(row[1])


async def _migrate_from_json():
    json_path = Path(__file__).parent / "tts_channels.json"
    if not json_path.exists():
        return

    data = json.loads(json_path.read_text())
    for guild_id_str, channel_ids in data.items():
        for channel_id in channel_ids:
            await _db.execute(
                "INSERT OR IGNORE INTO tts_channels (guild_id, channel_id) VALUES (?, ?)",
                (int(guild_id_str), int(channel_id)),
            )
    await _db.commit()
    await _refresh_cache()
    json_path.unlink()
    log.info("tts_channels.json -> SQLite 마이그레이션 완료")


# ── TTS 채널 ──

def get_tts_channels_cached(guild_id: int) -> list[int]:
    return _tts_channels_cache.get(guild_id, [])


async def add_tts_channel(guild_id: int, channel_id: int) -> bool:
    try:
        await _db.execute(
            "INSERT INTO tts_channels (guild_id, channel_id) VALUES (?, ?)",
            (guild_id, channel_id),
        )
        await _db.commit()
        _tts_channels_cache.setdefault(guild_id, []).append(channel_id)
        return True
    except aiosqlite.IntegrityError:
        return False


async def remove_tts_channel(guild_id: int, channel_id: int) -> bool:
    cursor = await _db.execute(
        "DELETE FROM tts_channels WHERE guild_id = ? AND channel_id = ?",
        (guild_id, channel_id),
    )
    await _db.commit()
    if cursor.rowcount > 0:
        channels = _tts_channels_cache.get(guild_id, [])
        if channel_id in channels:
            channels.remove(channel_id)
        return True
    return False


async def get_tts_channels(guild_id: int) -> list[int]:
    async with _db.execute(
        "SELECT channel_id FROM tts_channels WHERE guild_id = ?", (guild_id,)
    ) as cursor:
        return [row[0] async for row in cursor]


async def get_all_tts_channel_count() -> int:
    async with _db.execute("SELECT COUNT(DISTINCT guild_id) FROM tts_channels") as cursor:
        row = await cursor.fetchone()
        return row[0]


async def get_total_tts_channel_count() -> int:
    async with _db.execute("SELECT COUNT(*) FROM tts_channels") as cursor:
        row = await cursor.fetchone()
        return row[0]


async def get_tts_channel_counts_by_guild() -> dict[int, int]:
    async with _db.execute("""
        SELECT guild_id, COUNT(*) AS channel_count
        FROM tts_channels
        GROUP BY guild_id
    """) as cursor:
        return {row[0]: row[1] async for row in cursor}


async def purge_old_daily_stats(reference_day: date | None = None):
    if reference_day is None:
        reference_day = datetime.now(KST).date()
    cutoff_day = reference_day - timedelta(days=DAILY_STATS_RETENTION_DAYS - 1)
    await _db.execute(
        "DELETE FROM daily_stats WHERE day < ?",
        (cutoff_day.isoformat(),),
    )
    await _db.commit()


async def record_daily_snapshot(guild_count: int, active_channel_count: int, target_day: date | None = None):
    await purge_old_daily_stats(target_day)
    await _db.execute(
        """
        INSERT INTO daily_stats (day, tts_requests, guild_count, active_channel_count)
        VALUES (?, 0, ?, ?)
        ON CONFLICT(day) DO UPDATE SET
            guild_count = excluded.guild_count,
            active_channel_count = excluded.active_channel_count
        """,
        (_day_key(target_day), guild_count, active_channel_count),
    )
    await _db.commit()


async def increment_daily_tts_requests(count: int = 1, target_day: date | None = None):
    await purge_old_daily_stats(target_day)
    await _db.execute(
        """
        INSERT INTO daily_stats (day, tts_requests, guild_count, active_channel_count)
        VALUES (?, ?, 0, 0)
        ON CONFLICT(day) DO UPDATE SET
            tts_requests = tts_requests + excluded.tts_requests
        """,
        (_day_key(target_day), count),
    )
    await _db.commit()


async def get_daily_stats(target_day: date | None = None) -> dict:
    async with _db.execute(
        """
        SELECT tts_requests, guild_count, active_channel_count
        FROM daily_stats
        WHERE day = ?
        """,
        (_day_key(target_day),),
    ) as cursor:
        row = await cursor.fetchone()

    if not row:
        return {"day": _day_key(target_day), "tts_requests": 0, "guild_count": 0, "active_channel_count": 0}

    return {
        "day": _day_key(target_day),
        "tts_requests": row[0],
        "guild_count": row[1],
        "active_channel_count": row[2],
    }


async def get_recent_daily_stats(days: int = 7) -> list[dict]:
    today = datetime.now(KST).date()
    stats = []
    for offset in range(days - 1, -1, -1):
        target_day = today - timedelta(days=offset)
        daily = await get_daily_stats(target_day)
        request_count = daily["tts_requests"]
        stats.append(
            {
                "day": daily["day"],
                "label": target_day.strftime("%m-%d"),
                "tts_requests": request_count,
                "guild_count": daily["guild_count"],
                "active_channel_count": daily["active_channel_count"],
                "bar_width": 0 if request_count == 0 else min(max(request_count * 10, 8), 100),
            }
        )
    return stats


async def get_dashboard_metrics(guild_count: int, active_channel_count: int) -> dict:
    today = datetime.now(KST).date()
    yesterday = today - timedelta(days=1)

    await record_daily_snapshot(guild_count, active_channel_count, today)

    today_stats = await get_daily_stats(today)
    yesterday_stats = await get_daily_stats(yesterday)

    return {
        "guild_count": guild_count,
        "guild_delta": guild_count - yesterday_stats["guild_count"],
        "active_channel_count": active_channel_count,
        "active_channel_delta": active_channel_count - yesterday_stats["active_channel_count"],
        "daily_requests": today_stats["tts_requests"],
        "daily_requests_yesterday": yesterday_stats["tts_requests"],
        "recent_requests": await get_recent_daily_stats(),
    }


# ── 유저 설정 ──

async def get_user_settings(user_id: int) -> dict:
    async with _db.execute(
        "SELECT voice, speed, lang, total_steps FROM user_settings WHERE user_id = ?",
        (user_id,),
    ) as cursor:
        row = await cursor.fetchone()
        if row:
            return {"voice": row[0], "speed": row[1], "lang": row[2], "total_steps": row[3]}
    return dict(DEFAULT_USER_SETTINGS)


async def set_user_setting(user_id: int, **kwargs):
    current = await get_user_settings(user_id)
    current.update(kwargs)
    await _db.execute(
        """INSERT INTO user_settings (user_id, voice, speed, lang, total_steps)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               voice=excluded.voice, speed=excluded.speed,
               lang=excluded.lang, total_steps=excluded.total_steps""",
        (user_id, current["voice"], current["speed"], current["lang"], current["total_steps"]),
    )
    await _db.commit()
