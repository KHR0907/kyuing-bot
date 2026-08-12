import asyncio
import json
import os
import sqlite3
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite
from loguru import logger as log

from config import DAILY_STATS_RETENTION_DAYS, DATABASE_PATH, DEFAULT_USER_SETTINGS

_db: aiosqlite.Connection | None = None
_db_access_lock = asyncio.Lock()
_db_access_depth: ContextVar[int] = ContextVar("db_access_depth", default=0)
CURRENT_BOT_ID = int(os.getenv("KYUING_BOT_ID", os.getenv("BOT_ID", "1")))

_tts_channels_cache: dict[int, dict[int, list[int]]] = {}
_global_keyword_cache: dict[int, dict[str, str]] = {}
_guild_keyword_cache: dict[int, dict[int, dict[str, str]]] = {}
_pending_hits: dict[tuple[int, str, int | None, str], tuple[int, str]] = {}
KST = ZoneInfo("Asia/Seoul")


def set_current_bot_id(bot_id: int):
    global CURRENT_BOT_ID
    CURRENT_BOT_ID = int(bot_id)


def current_bot_id() -> int:
    return CURRENT_BOT_ID


def is_ready() -> bool:
    return _db is not None


def _bot_id(bot_id: int | None = None) -> int:
    return int(bot_id if bot_id is not None else CURRENT_BOT_ID)


def _day_key(target_day: date | None = None) -> str:
    if target_day is None:
        target_day = datetime.now(KST).date()
    return target_day.isoformat()


def _serialized_db_access(function):
    """Serialize use of the process-wide connection across async tasks.

    SQLite transactions belong to a connection, not an asyncio task.  Without
    this gate, a read cursor from one task can overlap a write in another task
    and leave the shared connection on a stale WAL snapshot.  Nested database
    API calls remain safe through the context-local depth counter.
    """
    @wraps(function)
    async def wrapper(*args, **kwargs):
        depth = _db_access_depth.get()
        if depth:
            token = _db_access_depth.set(depth + 1)
            try:
                return await function(*args, **kwargs)
            finally:
                _db_access_depth.reset(token)

        async with _db_access_lock:
            token = _db_access_depth.set(1)
            try:
                return await function(*args, **kwargs)
            except BaseException:
                if _db is not None and _db.in_transaction:
                    await _db.rollback()
                raise
            finally:
                _db_access_depth.reset(token)

    return wrapper


async def _table_columns(table: str) -> set[str]:
    async with _db.execute(f"PRAGMA table_info({table})") as cursor:
        return {row[1] async for row in cursor}


async def _table_exists(table: str) -> bool:
    async with _db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)) as cursor:
        return await cursor.fetchone() is not None


async def _create_multibot_tables():
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            token TEXT NOT NULL,
            discord_bot_user_id INTEGER UNIQUE,
            discord_username TEXT,
            enabled INTEGER DEFAULT 1,
            desired_state TEXT NOT NULL DEFAULT 'running',
            status TEXT DEFAULT 'stopped',
            pid INTEGER,
            guild_count INTEGER DEFAULT 0,
            last_started_at TEXT,
            last_stopped_at TEXT,
            last_error TEXT,
            created_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    table_sql = {
        "user_settings_v2": """
            CREATE TABLE IF NOT EXISTS user_settings_v2 (
                bot_id INTEGER NOT NULL DEFAULT 1, user_id INTEGER NOT NULL,
                engine TEXT DEFAULT 'supertonic', voice TEXT DEFAULT 'M1', speed REAL DEFAULT 1.0,
                lang TEXT DEFAULT 'ko', total_steps INTEGER DEFAULT 8,
                PRIMARY KEY (bot_id, user_id))
        """,
        "tts_char_usage_v2": """
            CREATE TABLE IF NOT EXISTS tts_char_usage_v2 (
                bot_id INTEGER NOT NULL DEFAULT 1, voice_type TEXT NOT NULL, month TEXT NOT NULL,
                char_count INTEGER DEFAULT 0, PRIMARY KEY (bot_id, voice_type, month))
        """,
        "tts_channels_v2": """
            CREATE TABLE IF NOT EXISTS tts_channels_v2 (
                bot_id INTEGER NOT NULL DEFAULT 1, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
                PRIMARY KEY (bot_id, guild_id, channel_id))
        """,
        "daily_stats_v2": """
            CREATE TABLE IF NOT EXISTS daily_stats_v2 (
                bot_id INTEGER NOT NULL DEFAULT 1, day TEXT NOT NULL, tts_requests INTEGER DEFAULT 0,
                guild_count INTEGER DEFAULT 0, active_channel_count INTEGER DEFAULT 0,
                PRIMARY KEY (bot_id, day))
        """,
        "global_keyword_aliases_v2": """
            CREATE TABLE IF NOT EXISTS global_keyword_aliases_v2 (
                bot_id INTEGER NOT NULL DEFAULT 1, keyword TEXT NOT NULL, replacement TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, hit_count INTEGER DEFAULT 0, last_seen_at TEXT,
                PRIMARY KEY (bot_id, keyword))
        """,
        "guild_keyword_aliases_v2": """
            CREATE TABLE IF NOT EXISTS guild_keyword_aliases_v2 (
                bot_id INTEGER NOT NULL DEFAULT 1, guild_id INTEGER NOT NULL, keyword TEXT NOT NULL,
                replacement TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                hit_count INTEGER DEFAULT 0, last_seen_at TEXT,
                PRIMARY KEY (bot_id, guild_id, keyword))
        """,
        "pronunciation_audit_v2": """
            CREATE TABLE IF NOT EXISTS pronunciation_audit_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id INTEGER NOT NULL DEFAULT 1,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP, actor_id INTEGER NOT NULL,
                action TEXT NOT NULL, scope TEXT NOT NULL, guild_id INTEGER, keyword TEXT NOT NULL,
                old_keyword TEXT, old_replacement TEXT, new_replacement TEXT)
        """,
        "sounds": """
            CREATE TABLE IF NOT EXISTS sounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id INTEGER NOT NULL DEFAULT 1,
                scope TEXT NOT NULL CHECK(scope IN ('global', 'guild')), guild_id INTEGER,
                keyword TEXT NOT NULL, filename TEXT NOT NULL, duration_seconds REAL NOT NULL,
                original_filename TEXT, created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                play_count INTEGER NOT NULL DEFAULT 0)
        """,
    }
    for sql in table_sql.values():
        await _db.execute(sql)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS dashboard_admins (
            user_id INTEGER PRIMARY KEY,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS bot_config_revisions (
            bot_id INTEGER PRIMARY KEY,
            revision INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP)
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS bot_guild_snapshots (
            bot_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            icon_url TEXT,
            member_count INTEGER NOT NULL DEFAULT 0,
            voice_channel_id INTEGER,
            voice_channel_name TEXT,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (bot_id, guild_id))
    """)
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_audit_v2_timestamp ON pronunciation_audit_v2 (timestamp DESC)")
    # SQLite UNIQUE 제약은 NULL을 서로 다른 값으로 취급하므로 (전역 음원 guild_id=NULL 중복 방지)
    # COALESCE 식 인덱스로 유니크를 강제한다
    await _db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sounds_unique ON sounds (bot_id, scope, COALESCE(guild_id, 0), keyword)"
    )
    # resolve_sound 핫패스용 조회 인덱스 (COALESCE 식 유니크 인덱스는 일반 동등 조회에 쓰이지 못함)
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sounds_guild_lookup ON sounds (bot_id, guild_id, keyword) WHERE scope = 'guild'"
    )
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sounds_global_lookup ON sounds (bot_id, keyword) WHERE scope = 'global'"
    )


async def _copy_if_old_exists(old: str, new: str, cols: list[str]):
    if not await _table_exists(old):
        return
    async with _db.execute(f"SELECT COUNT(*) FROM {new}") as cursor:
        if (await cursor.fetchone())[0] > 0:
            return
    old_cols = await _table_columns(old)
    available = [c for c in cols if c in old_cols]
    if not available:
        return
    dst = ", ".join(["bot_id", *available])
    src = ", ".join(available)
    await _db.execute(f"INSERT OR IGNORE INTO {new} ({dst}) SELECT 1, {src} FROM {old}")


async def _migrate_existing_data_to_multibot():
    await _create_multibot_tables()
    bot_columns = await _table_columns("bots")
    if "desired_state" not in bot_columns:
        await _db.execute(
            "ALTER TABLE bots ADD COLUMN desired_state TEXT NOT NULL DEFAULT 'running'"
        )
    token = os.getenv("DISCORD_TOKEN", "")
    await _db.execute(
        "INSERT OR IGNORE INTO bots (id, name, token, enabled, status) VALUES (1, 'Default Bot', ?, 1, 'stopped')",
        (token,),
    )
    await _db.execute(
        "INSERT OR IGNORE INTO bot_config_revisions (bot_id, revision) SELECT id, 0 FROM bots"
    )
    if token:
        await _db.execute("UPDATE bots SET token = CASE WHEN token = '' THEN ? ELSE token END WHERE id = 1", (token,))
    await _copy_if_old_exists("user_settings", "user_settings_v2", ["user_id", "engine", "voice", "speed", "lang", "total_steps"])
    # Supertonic 3 권장 기본 스텝(8)으로 v2 시절 저장된 값(<5) 끌어올리기
    await _db.execute("UPDATE user_settings_v2 SET total_steps = 8 WHERE total_steps < 5")
    await _copy_if_old_exists("tts_char_usage", "tts_char_usage_v2", ["voice_type", "month", "char_count"])
    await _copy_if_old_exists("tts_channels", "tts_channels_v2", ["guild_id", "channel_id"])
    await _copy_if_old_exists("daily_stats", "daily_stats_v2", ["day", "tts_requests", "guild_count", "active_channel_count"])
    await _copy_if_old_exists("global_keyword_aliases", "global_keyword_aliases_v2", ["keyword", "replacement", "created_at", "hit_count", "last_seen_at"])
    await _copy_if_old_exists("guild_keyword_aliases", "guild_keyword_aliases_v2", ["guild_id", "keyword", "replacement", "created_at", "hit_count", "last_seen_at"])
    await _copy_if_old_exists("pronunciation_audit", "pronunciation_audit_v2", ["timestamp", "actor_id", "action", "scope", "guild_id", "keyword", "old_keyword", "old_replacement", "new_replacement"])
    await _db.commit()


async def init_db():
    global _db
    db_path = Path(DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(str(db_path))
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA busy_timeout=5000")
    await _migrate_existing_data_to_multibot()
    await purge_old_daily_stats()
    await _refresh_cache()
    await _migrate_from_json()
    log.info("데이터베이스 초기화 완료")


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None


async def _refresh_cache():
    global _tts_channels_cache, _global_keyword_cache, _guild_keyword_cache
    _tts_channels_cache = {}
    async with _db.execute("SELECT bot_id, guild_id, channel_id FROM tts_channels_v2") as cursor:
        async for row in cursor:
            _tts_channels_cache.setdefault(row[0], {}).setdefault(row[1], []).append(row[2])
    _global_keyword_cache = {}
    async with _db.execute("SELECT bot_id, keyword, replacement FROM global_keyword_aliases_v2") as cursor:
        async for row in cursor:
            _global_keyword_cache.setdefault(row[0], {})[row[1]] = row[2]
    _guild_keyword_cache = {}
    async with _db.execute("SELECT bot_id, guild_id, keyword, replacement FROM guild_keyword_aliases_v2") as cursor:
        async for row in cursor:
            _guild_keyword_cache.setdefault(row[0], {}).setdefault(row[1], {})[row[2]] = row[3]


async def _refresh_bot_cache(bot_id: int):
    """Reload one bot's process-local caches after another process changes config."""
    bid = int(bot_id)
    channels: dict[int, list[int]] = {}
    async with _db.execute(
        "SELECT guild_id, channel_id FROM tts_channels_v2 WHERE bot_id = ?", (bid,)
    ) as cursor:
        async for guild_id, channel_id in cursor:
            channels.setdefault(guild_id, []).append(channel_id)
    _tts_channels_cache[bid] = channels

    global_aliases: dict[str, str] = {}
    async with _db.execute(
        "SELECT keyword, replacement FROM global_keyword_aliases_v2 WHERE bot_id = ?", (bid,)
    ) as cursor:
        async for keyword, replacement in cursor:
            global_aliases[keyword] = replacement
    _global_keyword_cache[bid] = global_aliases

    guild_aliases: dict[int, dict[str, str]] = {}
    async with _db.execute(
        "SELECT guild_id, keyword, replacement FROM guild_keyword_aliases_v2 WHERE bot_id = ?", (bid,)
    ) as cursor:
        async for guild_id, keyword, replacement in cursor:
            guild_aliases.setdefault(guild_id, {})[keyword] = replacement
    _guild_keyword_cache[bid] = guild_aliases


async def get_config_revision(bot_id: int | None = None) -> int:
    bid = _bot_id(bot_id)
    async with _db.execute(
        "SELECT revision FROM bot_config_revisions WHERE bot_id = ?", (bid,)
    ) as cursor:
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def _bump_config_revision(bot_id: int) -> None:
    await _db.execute(
        """INSERT INTO bot_config_revisions (bot_id, revision, updated_at)
           VALUES (?, 1, CURRENT_TIMESTAMP)
           ON CONFLICT(bot_id) DO UPDATE SET
             revision = revision + 1,
             updated_at = CURRENT_TIMESTAMP""",
        (int(bot_id),),
    )


async def refresh_cache_if_changed(known_revision: int, bot_id: int | None = None) -> int:
    """Refresh local cache when the shared DB revision differs."""
    bid = _bot_id(bot_id)
    current_revision = await get_config_revision(bid)
    if current_revision != int(known_revision):
        await _refresh_bot_cache(bid)
    return current_revision


async def sync_bot_guild_snapshots(bot_id: int, guilds: list[dict]) -> None:
    """Replace a worker's Discord guild snapshot in an isolated write transaction.

    Worker processes share the SQLite database, while each process also has a
    long-lived connection used by unrelated async tasks.  Reusing that connection
    here can turn a short-lived WAL read snapshot into SQLITE_BUSY_SNAPSHOT when
    another process commits.  A dedicated connection plus BEGIN IMMEDIATE makes
    the snapshot transaction start as a writer and guarantees that a failed
    attempt cannot poison the process-wide connection.
    """
    bid = int(bot_id)
    seen_ids: list[int] = []
    now_iso = datetime.now(KST).isoformat()
    for attempt in range(3):
        connection = await aiosqlite.connect(str(DATABASE_PATH))
        try:
            await connection.execute("PRAGMA busy_timeout=5000")
            await connection.execute("BEGIN IMMEDIATE")
            for guild in guilds:
                guild_id = int(guild["id"])
                seen_ids.append(guild_id)
                await connection.execute(
                    """INSERT INTO bot_guild_snapshots
                       (bot_id, guild_id, name, icon_url, member_count, voice_channel_id,
                        voice_channel_name, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(bot_id, guild_id) DO UPDATE SET
                         name=excluded.name, icon_url=excluded.icon_url,
                         member_count=excluded.member_count,
                         voice_channel_id=excluded.voice_channel_id,
                         voice_channel_name=excluded.voice_channel_name,
                         last_seen_at=excluded.last_seen_at""",
                    (
                        bid, guild_id, guild["name"], guild.get("icon_url"),
                        int(guild.get("member_count") or 0), guild.get("voice_channel_id"),
                        guild.get("voice_channel_name"), now_iso,
                    ),
                )
            if seen_ids:
                placeholders = ",".join("?" for _ in seen_ids)
                await connection.execute(
                    f"DELETE FROM bot_guild_snapshots WHERE bot_id = ? AND guild_id NOT IN ({placeholders})",
                    (bid, *seen_ids),
                )
            else:
                await connection.execute(
                    "DELETE FROM bot_guild_snapshots WHERE bot_id = ?", (bid,)
                )
            await connection.commit()
            return
        except aiosqlite.OperationalError as exc:
            await connection.rollback()
            error_code = getattr(exc, "sqlite_errorcode", 0)
            if error_code & 0xFF != sqlite3.SQLITE_BUSY or attempt == 2:
                raise
            await asyncio.sleep(0.05 * (2**attempt))
        except BaseException:
            await connection.rollback()
            raise
        finally:
            await connection.close()
            seen_ids.clear()


async def get_bot_guild_snapshots(bot_id: int) -> list[dict]:
    async with _db.execute(
        """SELECT guild_id, name, icon_url, member_count, voice_channel_id,
                  voice_channel_name, last_seen_at
           FROM bot_guild_snapshots WHERE bot_id = ? ORDER BY name COLLATE NOCASE""",
        (int(bot_id),),
    ) as cursor:
        return [
            {
                "id": row[0], "name": row[1], "icon_url": row[2] or "",
                "member_count": row[3] or 0, "voice_channel_id": row[4],
                "voice_channel_name": row[5], "last_seen_at": row[6],
            }
            async for row in cursor
        ]


async def get_bot_guild_snapshot(bot_id: int, guild_id: int) -> dict | None:
    snapshots = await get_bot_guild_snapshots(bot_id)
    return next((item for item in snapshots if item["id"] == int(guild_id)), None)


async def _migrate_from_json():
    json_path = Path(__file__).parent / "tts_channels.json"
    if not json_path.exists():
        return
    data = json.loads(json_path.read_text())
    for guild_id_str, channel_ids in data.items():
        for channel_id in channel_ids:
            await _db.execute("INSERT OR IGNORE INTO tts_channels_v2 (bot_id, guild_id, channel_id) VALUES (?, ?, ?)", (1, int(guild_id_str), int(channel_id)))
    await _db.commit()
    await _refresh_cache()
    json_path.unlink()
    log.info("tts_channels.json -> SQLite 마이그레이션 완료")


async def get_bots(include_disabled: bool = True) -> list[dict]:
    sql = """SELECT id, name, discord_bot_user_id, discord_username, enabled, desired_state, status, pid,
                    guild_count, last_started_at, last_stopped_at, last_error, created_by, created_at, updated_at FROM bots"""
    if not include_disabled:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY id ASC"
    async with _db.execute(sql) as cursor:
        return [{"id": row[0], "name": row[1], "discord_bot_user_id": row[2],
                 "discord_username": row[3], "enabled": bool(row[4]), "desired_state": row[5],
                 "status": row[6], "pid": row[7], "guild_count": row[8] or 0,
                 "last_started_at": row[9], "last_stopped_at": row[10], "last_error": row[11],
                 "created_by": row[12], "created_at": row[13], "updated_at": row[14]}
                async for row in cursor]


async def get_enabled_bots() -> list[dict]:
    return [bot for bot in await get_bots(include_disabled=False) if bot["desired_state"] == "running"]


async def get_bot(bot_id: int) -> dict | None:
    async with _db.execute("""SELECT id, name, discord_bot_user_id, discord_username, enabled, desired_state, status, pid,
                    guild_count, last_started_at, last_stopped_at, last_error, created_by, created_at, updated_at FROM bots WHERE id = ?""", (int(bot_id),)) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "discord_bot_user_id": row[2],
            "discord_username": row[3], "enabled": bool(row[4]), "desired_state": row[5],
            "status": row[6], "pid": row[7], "guild_count": row[8] or 0,
            "last_started_at": row[9], "last_stopped_at": row[10], "last_error": row[11],
            "created_by": row[12], "created_at": row[13], "updated_at": row[14]}


async def get_bot_token(bot_id: int) -> str | None:
    async with _db.execute("SELECT token FROM bots WHERE id = ?", (int(bot_id),)) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else None


async def create_bot(name: str, token: str, *, created_by: int | None = None, discord_bot_user_id: int | None = None, discord_username: str | None = None) -> dict | None:
    try:
        cursor = await _db.execute(
            """INSERT INTO bots (name, token, discord_bot_user_id, discord_username, enabled, desired_state, status, created_by, updated_at)
               VALUES (?, ?, ?, ?, 1, 'running', 'stopped', ?, CURRENT_TIMESTAMP)""",
            ((name or discord_username or "KYUING Bot").strip(), token, discord_bot_user_id, discord_username, created_by),
        )
        await _db.execute(
            "INSERT INTO bot_config_revisions (bot_id, revision) VALUES (?, 0)",
            (cursor.lastrowid,),
        )
        await _db.commit()
    except aiosqlite.IntegrityError:
        await _db.rollback()
        return None
    return await get_bot(cursor.lastrowid)


async def set_bot_enabled(bot_id: int, enabled: bool) -> bool:
    cursor = await _db.execute("UPDATE bots SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (1 if enabled else 0, int(bot_id)))
    await _db.commit()
    return cursor.rowcount > 0


async def set_bot_desired_state(bot_id: int, desired_state: str) -> bool:
    if desired_state not in {"running", "stopped"}:
        raise ValueError("desired_state must be running or stopped")
    cursor = await _db.execute(
        "UPDATE bots SET desired_state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (desired_state, int(bot_id)),
    )
    await _db.commit()
    return cursor.rowcount > 0


async def update_bot_runtime_status(bot_id: int, status: str, pid: int | None = None, last_error: str | None = None, guild_count: int | None = None) -> bool:
    sets = ["status = ?", "pid = ?", "last_error = ?", "updated_at = CURRENT_TIMESTAMP"]
    params: list = [status, pid, last_error]
    if guild_count is not None:
        sets.append("guild_count = ?")
        params.append(guild_count)
    if status == "running":
        sets.append("last_started_at = CURRENT_TIMESTAMP")
    if status == "stopped":
        sets.append("last_stopped_at = CURRENT_TIMESTAMP")
    params.append(int(bot_id))
    cursor = await _db.execute(f"UPDATE bots SET {', '.join(sets)} WHERE id = ?", params)
    await _db.commit()
    return cursor.rowcount > 0


def get_tts_channels_cached(guild_id: int, bot_id: int | None = None) -> list[int]:
    return _tts_channels_cache.get(_bot_id(bot_id), {}).get(guild_id, [])


async def add_tts_channel(guild_id: int, channel_id: int, bot_id: int | None = None) -> bool:
    bid = _bot_id(bot_id)
    try:
        await _db.execute("INSERT INTO tts_channels_v2 (bot_id, guild_id, channel_id) VALUES (?, ?, ?)", (bid, guild_id, channel_id))
        await _bump_config_revision(bid)
        await _db.commit()
        _tts_channels_cache.setdefault(bid, {}).setdefault(guild_id, []).append(channel_id)
        return True
    except aiosqlite.IntegrityError:
        await _db.rollback()
        return False


async def remove_tts_channel(guild_id: int, channel_id: int, bot_id: int | None = None) -> bool:
    bid = _bot_id(bot_id)
    cursor = await _db.execute("DELETE FROM tts_channels_v2 WHERE bot_id = ? AND guild_id = ? AND channel_id = ?", (bid, guild_id, channel_id))
    if cursor.rowcount > 0:
        await _bump_config_revision(bid)
    await _db.commit()
    if cursor.rowcount > 0:
        channels = _tts_channels_cache.get(bid, {}).get(guild_id, [])
        if channel_id in channels:
            channels.remove(channel_id)
        return True
    return False


async def get_tts_channels(guild_id: int, bot_id: int | None = None) -> list[int]:
    async with _db.execute("SELECT channel_id FROM tts_channels_v2 WHERE bot_id = ? AND guild_id = ?", (_bot_id(bot_id), guild_id)) as cursor:
        return [row[0] async for row in cursor]


async def get_all_tts_channel_count(bot_id: int | None = None) -> int:
    if bot_id is None:
        async with _db.execute("SELECT COUNT(DISTINCT bot_id || ':' || guild_id) FROM tts_channels_v2") as cursor:
            return (await cursor.fetchone())[0]
    async with _db.execute("SELECT COUNT(DISTINCT guild_id) FROM tts_channels_v2 WHERE bot_id = ?", (_bot_id(bot_id),)) as cursor:
        return (await cursor.fetchone())[0]


async def get_total_tts_channel_count(bot_id: int | None = None) -> int:
    if bot_id is None:
        async with _db.execute("SELECT COUNT(*) FROM tts_channels_v2") as cursor:
            return (await cursor.fetchone())[0]
    async with _db.execute("SELECT COUNT(*) FROM tts_channels_v2 WHERE bot_id = ?", (_bot_id(bot_id),)) as cursor:
        return (await cursor.fetchone())[0]


async def get_tts_channel_counts_by_guild(bot_id: int | None = None) -> dict[int, int]:
    async with _db.execute("SELECT guild_id, COUNT(*) FROM tts_channels_v2 WHERE bot_id = ? GROUP BY guild_id", (_bot_id(bot_id),)) as cursor:
        return {row[0]: row[1] async for row in cursor}


async def get_dashboard_admin_ids() -> list[int]:
    async with _db.execute("SELECT user_id FROM dashboard_admins ORDER BY created_at ASC, user_id ASC") as cursor:
        return [row[0] async for row in cursor]


async def add_dashboard_admin(user_id: int) -> bool:
    try:
        await _db.execute("INSERT INTO dashboard_admins (user_id) VALUES (?)", (user_id,))
        await _db.commit()
        return True
    except aiosqlite.IntegrityError:
        await _db.rollback()
        return False


async def remove_dashboard_admin(user_id: int) -> bool:
    cursor = await _db.execute("DELETE FROM dashboard_admins WHERE user_id = ?", (user_id,))
    await _db.commit()
    return cursor.rowcount > 0


def resolve_keyword_replacement(guild_id: int, text: str, bot_id: int | None = None) -> tuple[str, str | None]:
    bid = _bot_id(bot_id)
    guild_replacements = _guild_keyword_cache.get(bid, {}).get(guild_id, {})
    if text in guild_replacements:
        return guild_replacements[text], "guild"
    if text in _global_keyword_cache.get(bid, {}):
        return _global_keyword_cache[bid][text], "global"
    return text, None


def record_keyword_hit(scope: str, keyword: str, guild_id: int | None = None, bot_id: int | None = None) -> None:
    if scope not in ("global", "guild") or (scope == "guild" and guild_id is None):
        return
    bid = _bot_id(bot_id)
    key = (bid, scope, guild_id if scope == "guild" else None, keyword)
    now_iso = datetime.now(KST).isoformat()
    prev_count = _pending_hits.get(key, (0, now_iso))[0]
    _pending_hits[key] = (prev_count + 1, now_iso)


async def flush_keyword_hits() -> int:
    global _pending_hits
    if not _pending_hits:
        return 0
    pending = _pending_hits
    _pending_hits = {}
    try:
        for (bid, scope, guild_id, keyword), (count, last_seen) in pending.items():
            if scope == "guild":
                await _db.execute("UPDATE guild_keyword_aliases_v2 SET hit_count = hit_count + ?, last_seen_at = ? WHERE bot_id = ? AND guild_id = ? AND keyword = ?", (count, last_seen, bid, guild_id, keyword))
            else:
                await _db.execute("UPDATE global_keyword_aliases_v2 SET hit_count = hit_count + ?, last_seen_at = ? WHERE bot_id = ? AND keyword = ?", (count, last_seen, bid, keyword))
        await _db.commit()
        return len(pending)
    except Exception:
        await _db.rollback()
        for k, (cnt, ts) in pending.items():
            existing = _pending_hits.get(k, (0, ts))
            _pending_hits[k] = (existing[0] + cnt, ts)
        raise


async def _write_audit(actor_id: int, action: str, scope: str, keyword: str, guild_id: int | None = None, old_keyword: str | None = None, old_replacement: str | None = None, new_replacement: str | None = None, bot_id: int | None = None):
    if actor_id is None:
        return
    await _db.execute(
        """INSERT INTO pronunciation_audit_v2
           (bot_id, actor_id, action, scope, guild_id, keyword, old_keyword, old_replacement, new_replacement)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (_bot_id(bot_id), actor_id, action, scope, guild_id, keyword, old_keyword, old_replacement, new_replacement),
    )


async def get_audit_log(limit: int = 100, bot_id: int | None = None) -> list[dict]:
    where = "WHERE bot_id = ?" if bot_id is not None else ""
    params = [_bot_id(bot_id)] if bot_id is not None else []
    params.append(limit)
    async with _db.execute(f"""SELECT id, timestamp, actor_id, action, scope, guild_id, keyword, old_keyword,
                                  old_replacement, new_replacement, bot_id FROM pronunciation_audit_v2
                                  {where} ORDER BY id DESC LIMIT ?""", params) as cursor:
        return [{"id": row[0], "timestamp": row[1], "actor_id": row[2], "action": row[3], "scope": row[4],
                 "guild_id": row[5], "keyword": row[6], "old_keyword": row[7], "old_replacement": row[8],
                 "new_replacement": row[9], "bot_id": row[10]} async for row in cursor]


async def get_global_keyword_aliases(bot_id: int | None = None) -> list[dict]:
    async with _db.execute("""SELECT keyword, replacement, hit_count, last_seen_at, created_at, bot_id
                              FROM global_keyword_aliases_v2 WHERE bot_id = ? ORDER BY created_at ASC, keyword ASC""", (_bot_id(bot_id),)) as cursor:
        return [{"keyword": row[0], "replacement": row[1], "hit_count": row[2] or 0, "last_seen_at": row[3], "created_at": row[4], "bot_id": row[5]} async for row in cursor]


async def add_global_keyword_alias(keyword: str, replacement: str, *, audit_actor: int | None = None, bot_id: int | None = None) -> bool:
    bid = _bot_id(bot_id)
    try:
        await _db.execute("INSERT INTO global_keyword_aliases_v2 (bot_id, keyword, replacement) VALUES (?, ?, ?)", (bid, keyword, replacement))
    except aiosqlite.IntegrityError:
        await _db.rollback()
        return False
    if audit_actor is not None:
        await _write_audit(audit_actor, "add", "global", keyword, new_replacement=replacement, bot_id=bid)
    await _bump_config_revision(bid)
    await _db.commit()
    _global_keyword_cache.setdefault(bid, {})[keyword] = replacement
    return True


async def remove_global_keyword_alias(keyword: str, *, audit_actor: int | None = None, bot_id: int | None = None) -> bool:
    bid = _bot_id(bot_id)
    async with _db.execute("SELECT replacement FROM global_keyword_aliases_v2 WHERE bot_id = ? AND keyword = ?", (bid, keyword)) as cursor:
        existing = await cursor.fetchone()
    if existing is None:
        return False
    await _db.execute("DELETE FROM global_keyword_aliases_v2 WHERE bot_id = ? AND keyword = ?", (bid, keyword))
    if audit_actor is not None:
        await _write_audit(audit_actor, "delete", "global", keyword, old_replacement=existing[0], bot_id=bid)
    await _bump_config_revision(bid)
    await _db.commit()
    _global_keyword_cache.get(bid, {}).pop(keyword, None)
    return True


async def update_global_keyword_alias(original_keyword: str, keyword: str, replacement: str, *, audit_actor: int | None = None, bot_id: int | None = None) -> str:
    bid = _bot_id(bot_id)
    async with _db.execute("SELECT keyword, replacement FROM global_keyword_aliases_v2 WHERE bot_id = ? AND keyword = ?", (bid, original_keyword)) as cursor:
        existing = await cursor.fetchone()
    if existing is None:
        return "not_found"
    try:
        await _db.execute("UPDATE global_keyword_aliases_v2 SET keyword = ?, replacement = ? WHERE bot_id = ? AND keyword = ?", (keyword, replacement, bid, original_keyword))
    except aiosqlite.IntegrityError:
        await _db.rollback()
        return "conflict"
    if audit_actor is not None:
        await _write_audit(audit_actor, "update", "global", keyword, old_keyword=existing[0] if existing[0] != keyword else None, old_replacement=existing[1], new_replacement=replacement, bot_id=bid)
    await _bump_config_revision(bid)
    await _db.commit()
    if original_keyword != keyword:
        _global_keyword_cache.get(bid, {}).pop(original_keyword, None)
    _global_keyword_cache.setdefault(bid, {})[keyword] = replacement
    return "updated"


async def get_guild_keyword_aliases(bot_id: int | None = None) -> list[dict]:
    async with _db.execute("""SELECT guild_id, keyword, replacement, hit_count, last_seen_at, created_at, bot_id
                              FROM guild_keyword_aliases_v2 WHERE bot_id = ? ORDER BY guild_id ASC, created_at ASC, keyword ASC""", (_bot_id(bot_id),)) as cursor:
        return [{"guild_id": row[0], "keyword": row[1], "replacement": row[2], "hit_count": row[3] or 0, "last_seen_at": row[4], "created_at": row[5], "bot_id": row[6]} async for row in cursor]


async def get_guild_keyword_aliases_for(guild_id: int, bot_id: int | None = None) -> list[dict]:
    async with _db.execute("""SELECT keyword, replacement, hit_count, last_seen_at, created_at
                              FROM guild_keyword_aliases_v2 WHERE bot_id = ? AND guild_id = ? ORDER BY created_at ASC, keyword ASC""", (_bot_id(bot_id), guild_id)) as cursor:
        return [{"keyword": row[0], "replacement": row[1], "hit_count": row[2] or 0, "last_seen_at": row[3], "created_at": row[4]} async for row in cursor]


async def add_guild_keyword_alias(guild_id: int, keyword: str, replacement: str, *, audit_actor: int | None = None, bot_id: int | None = None) -> bool:
    bid = _bot_id(bot_id)
    try:
        await _db.execute("INSERT INTO guild_keyword_aliases_v2 (bot_id, guild_id, keyword, replacement) VALUES (?, ?, ?, ?)", (bid, guild_id, keyword, replacement))
    except aiosqlite.IntegrityError:
        await _db.rollback()
        return False
    if audit_actor is not None:
        await _write_audit(audit_actor, "add", "guild", keyword, guild_id=guild_id, new_replacement=replacement, bot_id=bid)
    await _bump_config_revision(bid)
    await _db.commit()
    _guild_keyword_cache.setdefault(bid, {}).setdefault(guild_id, {})[keyword] = replacement
    return True


async def remove_guild_keyword_alias(guild_id: int, keyword: str, *, audit_actor: int | None = None, bot_id: int | None = None) -> bool:
    bid = _bot_id(bot_id)
    async with _db.execute("SELECT replacement FROM guild_keyword_aliases_v2 WHERE bot_id = ? AND guild_id = ? AND keyword = ?", (bid, guild_id, keyword)) as cursor:
        existing = await cursor.fetchone()
    if existing is None:
        return False
    await _db.execute("DELETE FROM guild_keyword_aliases_v2 WHERE bot_id = ? AND guild_id = ? AND keyword = ?", (bid, guild_id, keyword))
    if audit_actor is not None:
        await _write_audit(audit_actor, "delete", "guild", keyword, guild_id=guild_id, old_replacement=existing[0], bot_id=bid)
    await _bump_config_revision(bid)
    await _db.commit()
    _guild_keyword_cache.get(bid, {}).get(guild_id, {}).pop(keyword, None)
    return True


async def update_guild_keyword_alias(guild_id: int, original_keyword: str, keyword: str, replacement: str, *, audit_actor: int | None = None, bot_id: int | None = None) -> str:
    bid = _bot_id(bot_id)
    async with _db.execute("SELECT keyword, replacement FROM guild_keyword_aliases_v2 WHERE bot_id = ? AND guild_id = ? AND keyword = ?", (bid, guild_id, original_keyword)) as cursor:
        existing = await cursor.fetchone()
    if existing is None:
        return "not_found"
    try:
        await _db.execute("UPDATE guild_keyword_aliases_v2 SET keyword = ?, replacement = ? WHERE bot_id = ? AND guild_id = ? AND keyword = ?", (keyword, replacement, bid, guild_id, original_keyword))
    except aiosqlite.IntegrityError:
        await _db.rollback()
        return "conflict"
    if audit_actor is not None:
        await _write_audit(audit_actor, "update", "guild", keyword, guild_id=guild_id, old_keyword=existing[0] if existing[0] != keyword else None, old_replacement=existing[1], new_replacement=replacement, bot_id=bid)
    await _bump_config_revision(bid)
    await _db.commit()
    guild_aliases = _guild_keyword_cache.setdefault(bid, {}).setdefault(guild_id, {})
    if original_keyword != keyword:
        guild_aliases.pop(original_keyword, None)
    guild_aliases[keyword] = replacement
    return "updated"


async def import_keyword_aliases_batch(rows: list[dict], actor_id: int, bot_id: int | None = None) -> tuple[int, int]:
    added = 0
    skipped = 0
    bid = _bot_id(bot_id)
    for row in rows:
        try:
            if row.get("scope") == "global":
                await _db.execute("INSERT INTO global_keyword_aliases_v2 (bot_id, keyword, replacement) VALUES (?, ?, ?)", (bid, row.get("keyword"), row.get("replacement")))
                await _write_audit(actor_id, "add", "global", row.get("keyword"), new_replacement=row.get("replacement"), bot_id=bid)
                _global_keyword_cache.setdefault(bid, {})[row.get("keyword")] = row.get("replacement")
                added += 1
            elif row.get("scope") == "guild":
                await _db.execute("INSERT INTO guild_keyword_aliases_v2 (bot_id, guild_id, keyword, replacement) VALUES (?, ?, ?, ?)", (bid, row.get("guild_id"), row.get("keyword"), row.get("replacement")))
                await _write_audit(actor_id, "add", "guild", row.get("keyword"), guild_id=row.get("guild_id"), new_replacement=row.get("replacement"), bot_id=bid)
                _guild_keyword_cache.setdefault(bid, {}).setdefault(row.get("guild_id"), {})[row.get("keyword")] = row.get("replacement")
                added += 1
            else:
                skipped += 1
        except aiosqlite.IntegrityError:
            skipped += 1
    if added:
        await _bump_config_revision(bid)
    await _db.commit()
    return added, skipped


async def purge_old_daily_stats(reference_day: date | None = None):
    if reference_day is None:
        reference_day = datetime.now(KST).date()
    cutoff_day = reference_day - timedelta(days=DAILY_STATS_RETENTION_DAYS - 1)
    await _db.execute("DELETE FROM daily_stats_v2 WHERE day < ?", (cutoff_day.isoformat(),))
    await _db.commit()


async def record_daily_snapshot(guild_count: int, active_channel_count: int, target_day: date | None = None, bot_id: int | None = None):
    bid = _bot_id(bot_id)
    await purge_old_daily_stats(target_day)
    await _db.execute("""INSERT INTO daily_stats_v2 (bot_id, day, tts_requests, guild_count, active_channel_count)
                         VALUES (?, ?, 0, ?, ?)
                         ON CONFLICT(bot_id, day) DO UPDATE SET guild_count = excluded.guild_count,
                         active_channel_count = excluded.active_channel_count""", (bid, _day_key(target_day), guild_count, active_channel_count))
    await _db.execute("UPDATE bots SET guild_count = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (guild_count, bid))
    await _db.commit()


async def increment_daily_tts_requests(count: int = 1, target_day: date | None = None, bot_id: int | None = None):
    bid = _bot_id(bot_id)
    await purge_old_daily_stats(target_day)
    await _db.execute("""INSERT INTO daily_stats_v2 (bot_id, day, tts_requests, guild_count, active_channel_count)
                         VALUES (?, ?, ?, 0, 0)
                         ON CONFLICT(bot_id, day) DO UPDATE SET tts_requests = tts_requests + excluded.tts_requests""", (bid, _day_key(target_day), count))
    await _db.commit()


async def get_daily_stats(target_day: date | None = None, bot_id: int | None = None) -> dict:
    async with _db.execute("SELECT tts_requests, guild_count, active_channel_count FROM daily_stats_v2 WHERE bot_id = ? AND day = ?", (_bot_id(bot_id), _day_key(target_day))) as cursor:
        row = await cursor.fetchone()
    if not row:
        return {"day": _day_key(target_day), "tts_requests": 0, "guild_count": 0, "active_channel_count": 0}
    return {"day": _day_key(target_day), "tts_requests": row[0], "guild_count": row[1], "active_channel_count": row[2]}


async def get_recent_daily_stats(days: int = 7, bot_id: int | None = None) -> list[dict]:
    today = datetime.now(KST).date()
    stats = []
    for offset in range(days - 1, -1, -1):
        target_day = today - timedelta(days=offset)
        daily = await get_daily_stats(target_day, bot_id=bot_id)
        request_count = daily["tts_requests"]
        stats.append({"day": daily["day"], "label": target_day.strftime("%m-%d"), "tts_requests": request_count,
                      "guild_count": daily["guild_count"], "active_channel_count": daily["active_channel_count"],
                      "bar_width": 0 if request_count == 0 else min(max(request_count * 10, 8), 100)})
    return stats


async def get_dashboard_metrics(guild_count: int, active_channel_count: int, bot_id: int | None = None) -> dict:
    today = datetime.now(KST).date()
    yesterday = today - timedelta(days=1)
    await record_daily_snapshot(guild_count, active_channel_count, today, bot_id=bot_id)
    today_stats = await get_daily_stats(today, bot_id=bot_id)
    yesterday_stats = await get_daily_stats(yesterday, bot_id=bot_id)
    return {"guild_count": guild_count, "guild_delta": guild_count - yesterday_stats["guild_count"],
            "active_channel_count": active_channel_count, "active_channel_delta": active_channel_count - yesterday_stats["active_channel_count"],
            "daily_requests": today_stats["tts_requests"], "daily_requests_yesterday": yesterday_stats["tts_requests"],
            "recent_requests": await get_recent_daily_stats(bot_id=bot_id)}


async def get_project_metrics() -> dict:
    today = _day_key()
    async with _db.execute("SELECT COUNT(*) FROM bots WHERE enabled = 1") as cursor:
        bot_count = (await cursor.fetchone())[0]
    async with _db.execute("SELECT COALESCE(SUM(tts_requests),0), COALESCE(SUM(guild_count),0), COALESCE(SUM(active_channel_count),0) FROM daily_stats_v2 WHERE day = ?", (today,)) as cursor:
        requests, guilds, channels = await cursor.fetchone()
    return {"bot_count": bot_count, "daily_requests": requests or 0, "guild_count": guilds or 0, "active_channel_count": channels or 0}


async def get_user_settings(user_id: int, bot_id: int | None = None) -> dict:
    async with _db.execute("SELECT engine, voice, speed, lang, total_steps FROM user_settings_v2 WHERE bot_id = ? AND user_id = ?", (_bot_id(bot_id), user_id)) as cursor:
        row = await cursor.fetchone()
        if row:
            return {"engine": row[0], "voice": row[1], "speed": row[2], "lang": row[3], "total_steps": row[4]}
    return dict(DEFAULT_USER_SETTINGS)


async def set_user_setting(user_id: int, bot_id: int | None = None, **kwargs):
    current = await get_user_settings(user_id, bot_id=bot_id)
    current.update(kwargs)
    await _db.execute("""INSERT INTO user_settings_v2 (bot_id, user_id, engine, voice, speed, lang, total_steps)
                         VALUES (?, ?, ?, ?, ?, ?, ?)
                         ON CONFLICT(bot_id, user_id) DO UPDATE SET engine=excluded.engine,
                         voice=excluded.voice, speed=excluded.speed, lang=excluded.lang, total_steps=excluded.total_steps""",
                      (_bot_id(bot_id), user_id, current["engine"], current["voice"], current["speed"], current["lang"], current["total_steps"]))
    await _db.commit()


async def increment_tts_char_usage(voice_type: str, char_count: int, bot_id: int | None = None):
    month = datetime.now(KST).strftime("%Y-%m")
    await _db.execute("""INSERT INTO tts_char_usage_v2 (bot_id, voice_type, month, char_count)
                         VALUES (?, ?, ?, ?)
                         ON CONFLICT(bot_id, voice_type, month) DO UPDATE SET char_count = char_count + excluded.char_count""",
                      (_bot_id(bot_id), voice_type, month, char_count))
    await _db.commit()


async def get_tts_char_usage(month: str | None = None, bot_id: int | None = None) -> dict[str, int]:
    if month is None:
        month = datetime.now(KST).strftime("%Y-%m")
    async with _db.execute("SELECT voice_type, char_count FROM tts_char_usage_v2 WHERE bot_id = ? AND month = ?", (_bot_id(bot_id), month)) as cursor:
        return {row[0]: row[1] async for row in cursor}


# ───────────────────────── Sounds (사운드보드) ─────────────────────────

_SOUND_COLS = "id, bot_id, scope, guild_id, keyword, filename, duration_seconds, original_filename, created_by, created_at, play_count"


def _sound_row_to_dict(row) -> dict:
    return {"id": row[0], "bot_id": row[1], "scope": row[2], "guild_id": row[3],
            "keyword": row[4], "filename": row[5], "duration_seconds": row[6],
            "original_filename": row[7], "created_by": row[8], "created_at": row[9],
            "play_count": row[10] or 0}


async def add_sound(scope: str, keyword: str, filename: str, duration_seconds: float, *,
                    guild_id: int | None = None, original_filename: str | None = None,
                    created_by: int | None = None, bot_id: int | None = None) -> dict | None:
    if scope not in ("global", "guild") or (scope == "guild" and guild_id is None):
        return None
    bid = _bot_id(bot_id)
    try:
        cursor = await _db.execute(
            """INSERT INTO sounds (bot_id, scope, guild_id, keyword, filename, duration_seconds, original_filename, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (bid, scope, guild_id if scope == "guild" else None, keyword, filename,
             duration_seconds, original_filename, created_by),
        )
        await _db.commit()
    except aiosqlite.IntegrityError:
        await _db.rollback()
        return None
    return await get_sound_by_id(cursor.lastrowid)


async def get_sound_by_id(sound_id: int) -> dict | None:
    async with _db.execute(f"SELECT {_SOUND_COLS} FROM sounds WHERE id = ?", (int(sound_id),)) as cursor:
        row = await cursor.fetchone()
    return _sound_row_to_dict(row) if row else None


async def resolve_sound(keyword: str, guild_id: int | None = None, bot_id: int | None = None) -> dict | None:
    """길드 음원 우선, 없으면 전역 음원."""
    bid = _bot_id(bot_id)
    if guild_id is not None:
        async with _db.execute(
            f"SELECT {_SOUND_COLS} FROM sounds WHERE bot_id = ? AND scope = 'guild' AND guild_id = ? AND keyword = ?",
            (bid, guild_id, keyword),
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            return _sound_row_to_dict(row)
    async with _db.execute(
        f"SELECT {_SOUND_COLS} FROM sounds WHERE bot_id = ? AND scope = 'global' AND keyword = ?",
        (bid, keyword),
    ) as cursor:
        row = await cursor.fetchone()
    return _sound_row_to_dict(row) if row else None


async def get_global_sounds(bot_id: int | None = None) -> list[dict]:
    async with _db.execute(
        f"SELECT {_SOUND_COLS} FROM sounds WHERE bot_id = ? AND scope = 'global' ORDER BY created_at ASC, keyword ASC",
        (_bot_id(bot_id),),
    ) as cursor:
        return [_sound_row_to_dict(row) async for row in cursor]


async def get_guild_sounds(guild_id: int | None = None, bot_id: int | None = None) -> list[dict]:
    """guild_id를 주면 해당 길드만, 없으면 모든 길드 음원 (대시보드용)."""
    bid = _bot_id(bot_id)
    if guild_id is None:
        sql = f"SELECT {_SOUND_COLS} FROM sounds WHERE bot_id = ? AND scope = 'guild' ORDER BY guild_id ASC, created_at ASC, keyword ASC"
        params: tuple = (bid,)
    else:
        sql = f"SELECT {_SOUND_COLS} FROM sounds WHERE bot_id = ? AND scope = 'guild' AND guild_id = ? ORDER BY created_at ASC, keyword ASC"
        params = (bid, guild_id)
    async with _db.execute(sql, params) as cursor:
        return [_sound_row_to_dict(row) async for row in cursor]


async def get_sounds_for_guild(guild_id: int, bot_id: int | None = None) -> list[dict]:
    """길드에서 사용 가능한 음원: 길드 음원 + 길드에 가려지지 않은 전역 음원."""
    guild_sounds = await get_guild_sounds(guild_id, bot_id=bot_id)
    guild_keywords = {s["keyword"] for s in guild_sounds}
    global_sounds = [s for s in await get_global_sounds(bot_id=bot_id) if s["keyword"] not in guild_keywords]
    return guild_sounds + global_sounds


async def get_guild_sound_count(guild_id: int, bot_id: int | None = None) -> int:
    async with _db.execute(
        "SELECT COUNT(*) FROM sounds WHERE bot_id = ? AND scope = 'guild' AND guild_id = ?",
        (_bot_id(bot_id), guild_id),
    ) as cursor:
        return (await cursor.fetchone())[0]


async def remove_sound(scope: str, keyword: str, *, guild_id: int | None = None, bot_id: int | None = None) -> dict | None:
    """삭제된 행을 반환한다 (디스크 파일 정리는 호출자 책임). 없으면 None."""
    if scope not in ("global", "guild") or (scope == "guild" and guild_id is None):
        return None
    bid = _bot_id(bot_id)
    if scope == "guild":
        sql = f"DELETE FROM sounds WHERE bot_id = ? AND scope = 'guild' AND guild_id = ? AND keyword = ? RETURNING {_SOUND_COLS}"
        params: tuple = (bid, guild_id, keyword)
    else:
        sql = f"DELETE FROM sounds WHERE bot_id = ? AND scope = 'global' AND keyword = ? RETURNING {_SOUND_COLS}"
        params = (bid, keyword)
    async with _db.execute(sql, params) as cursor:
        row = await cursor.fetchone()
    await _db.commit()
    return _sound_row_to_dict(row) if row else None


async def remove_sound_by_id(sound_id: int) -> dict | None:
    async with _db.execute(
        f"DELETE FROM sounds WHERE id = ? RETURNING {_SOUND_COLS}", (int(sound_id),),
    ) as cursor:
        row = await cursor.fetchone()
    await _db.commit()
    return _sound_row_to_dict(row) if row else None


async def increment_sound_play_count(sound_id: int):
    await _db.execute("UPDATE sounds SET play_count = play_count + 1 WHERE id = ?", (int(sound_id),))
    await _db.commit()


# All public APIs using the long-lived connection are serialized per process.
# Snapshot writes are excluded because they deliberately use an isolated
# connection and BEGIN IMMEDIATE to coordinate between worker processes.
_SERIALIZED_DB_APIS = (
    "get_config_revision",
    "refresh_cache_if_changed",
    "get_bot_guild_snapshots",
    "get_bot_guild_snapshot",
    "get_bots",
    "get_enabled_bots",
    "get_bot",
    "get_bot_token",
    "create_bot",
    "set_bot_enabled",
    "set_bot_desired_state",
    "update_bot_runtime_status",
    "add_tts_channel",
    "remove_tts_channel",
    "get_tts_channels",
    "get_all_tts_channel_count",
    "get_total_tts_channel_count",
    "get_tts_channel_counts_by_guild",
    "get_dashboard_admin_ids",
    "add_dashboard_admin",
    "remove_dashboard_admin",
    "flush_keyword_hits",
    "get_audit_log",
    "get_global_keyword_aliases",
    "add_global_keyword_alias",
    "remove_global_keyword_alias",
    "update_global_keyword_alias",
    "get_guild_keyword_aliases",
    "get_guild_keyword_aliases_for",
    "add_guild_keyword_alias",
    "remove_guild_keyword_alias",
    "update_guild_keyword_alias",
    "import_keyword_aliases_batch",
    "purge_old_daily_stats",
    "record_daily_snapshot",
    "increment_daily_tts_requests",
    "get_daily_stats",
    "get_recent_daily_stats",
    "get_dashboard_metrics",
    "get_project_metrics",
    "get_user_settings",
    "set_user_setting",
    "increment_tts_char_usage",
    "get_tts_char_usage",
    "add_sound",
    "get_sound_by_id",
    "resolve_sound",
    "get_global_sounds",
    "get_guild_sounds",
    "get_sounds_for_guild",
    "get_guild_sound_count",
    "remove_sound",
    "remove_sound_by_id",
    "increment_sound_play_count",
)

for _api_name in _SERIALIZED_DB_APIS:
    globals()[_api_name] = _serialized_db_access(globals()[_api_name])
