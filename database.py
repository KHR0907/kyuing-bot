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
_global_keyword_cache: dict[str, str] = {}
_guild_keyword_cache: dict[int, dict[str, str]] = {}
# 키워드 hit 누적 (scope, guild_id|None, keyword) -> (count, last_seen_iso)
# on_message 핫패스에서 commit하지 않고 메모리에만 누적, flush_keyword_hits()로 일괄 반영
_pending_hits: dict[tuple[str, int | None, str], tuple[int, str]] = {}
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
            engine TEXT DEFAULT 'supertonic',
            voice TEXT DEFAULT 'M1',
            speed REAL DEFAULT 1.0,
            lang TEXT DEFAULT 'ko',
            total_steps INTEGER DEFAULT 2
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS tts_char_usage (
            voice_type TEXT NOT NULL,
            month TEXT NOT NULL,
            char_count INTEGER DEFAULT 0,
            PRIMARY KEY (voice_type, month)
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
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS dashboard_admins (
            user_id INTEGER PRIMARY KEY,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS global_keyword_aliases (
            keyword TEXT PRIMARY KEY,
            replacement TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            hit_count INTEGER DEFAULT 0,
            last_seen_at TEXT
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS guild_keyword_aliases (
            guild_id INTEGER NOT NULL,
            keyword TEXT NOT NULL,
            replacement TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            hit_count INTEGER DEFAULT 0,
            last_seen_at TEXT,
            PRIMARY KEY (guild_id, keyword)
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS pronunciation_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            actor_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            scope TEXT NOT NULL,
            guild_id INTEGER,
            keyword TEXT NOT NULL,
            old_keyword TEXT,
            old_replacement TEXT,
            new_replacement TEXT
        )
    """)
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON pronunciation_audit (timestamp DESC)"
    )
    await _db.commit()

    # engine 컬럼 마이그레이션 (기존 DB 호환)
    async with _db.execute("PRAGMA table_info(user_settings)") as cursor:
        columns = {row[1] async for row in cursor}
    if "engine" not in columns:
        await _db.execute("ALTER TABLE user_settings ADD COLUMN engine TEXT DEFAULT 'supertonic'")
        await _db.commit()
        log.info("user_settings 테이블에 engine 컬럼 추가")

    # hit_count/last_seen_at 컬럼 마이그레이션 (기존 DB 호환)
    for table in ("global_keyword_aliases", "guild_keyword_aliases"):
        async with _db.execute(f"PRAGMA table_info({table})") as cursor:
            cols = {row[1] async for row in cursor}
        if "hit_count" not in cols:
            await _db.execute(f"ALTER TABLE {table} ADD COLUMN hit_count INTEGER DEFAULT 0")
        if "last_seen_at" not in cols:
            await _db.execute(f"ALTER TABLE {table} ADD COLUMN last_seen_at TEXT")
    async with _db.execute("PRAGMA table_info(pronunciation_audit)") as cursor:
        audit_cols = {row[1] async for row in cursor}
    if "old_keyword" not in audit_cols:
        await _db.execute("ALTER TABLE pronunciation_audit ADD COLUMN old_keyword TEXT")
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
    global _tts_channels_cache, _global_keyword_cache, _guild_keyword_cache
    _tts_channels_cache = {}
    async with _db.execute("SELECT guild_id, channel_id FROM tts_channels") as cursor:
        async for row in cursor:
            _tts_channels_cache.setdefault(row[0], []).append(row[1])

    _global_keyword_cache = {}
    async with _db.execute("SELECT keyword, replacement FROM global_keyword_aliases") as cursor:
        async for row in cursor:
            _global_keyword_cache[row[0]] = row[1]

    _guild_keyword_cache = {}
    async with _db.execute("SELECT guild_id, keyword, replacement FROM guild_keyword_aliases") as cursor:
        async for row in cursor:
            _guild_keyword_cache.setdefault(row[0], {})[row[1]] = row[2]


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


async def get_dashboard_admin_ids() -> list[int]:
    async with _db.execute(
        "SELECT user_id FROM dashboard_admins ORDER BY created_at ASC, user_id ASC"
    ) as cursor:
        return [row[0] async for row in cursor]


async def add_dashboard_admin(user_id: int) -> bool:
    try:
        await _db.execute(
            "INSERT INTO dashboard_admins (user_id) VALUES (?)",
            (user_id,),
        )
        await _db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def remove_dashboard_admin(user_id: int) -> bool:
    cursor = await _db.execute(
        "DELETE FROM dashboard_admins WHERE user_id = ?",
        (user_id,),
    )
    await _db.commit()
    return cursor.rowcount > 0


def resolve_keyword_replacement(guild_id: int, text: str) -> tuple[str, str | None]:
    guild_replacements = _guild_keyword_cache.get(guild_id, {})
    if text in guild_replacements:
        return guild_replacements[text], "guild"

    if text in _global_keyword_cache:
        return _global_keyword_cache[text], "global"

    return text, None


def record_keyword_hit(scope: str, keyword: str, guild_id: int | None = None) -> None:
    """on_message 핫패스용. DB 쓰기 없이 메모리에만 누적. 주기적으로 flush_keyword_hits()가 일괄 반영."""
    if scope not in ("global", "guild"):
        return
    if scope == "guild" and guild_id is None:
        return
    key = (scope, guild_id if scope == "guild" else None, keyword)
    now_iso = datetime.now(KST).isoformat()
    prev_count = _pending_hits.get(key, (0, now_iso))[0]
    _pending_hits[key] = (prev_count + 1, now_iso)


async def flush_keyword_hits() -> int:
    """누적된 hit를 DB에 일괄 반영. 단일 트랜잭션. 반환: flush된 키워드 수."""
    global _pending_hits
    if not _pending_hits:
        return 0
    pending = _pending_hits
    _pending_hits = {}

    try:
        for (scope, guild_id, keyword), (count, last_seen) in pending.items():
            if scope == "guild":
                await _db.execute(
                    """
                    UPDATE guild_keyword_aliases
                    SET hit_count = hit_count + ?, last_seen_at = ?
                    WHERE guild_id = ? AND keyword = ?
                    """,
                    (count, last_seen, guild_id, keyword),
                )
            else:
                await _db.execute(
                    """
                    UPDATE global_keyword_aliases
                    SET hit_count = hit_count + ?, last_seen_at = ?
                    WHERE keyword = ?
                    """,
                    (count, last_seen, keyword),
                )
        await _db.commit()
        return len(pending)
    except Exception:
        # 실패 시 누적분 복원 (이후 재시도)
        for k, (cnt, ts) in pending.items():
            existing = _pending_hits.get(k, (0, ts))
            _pending_hits[k] = (existing[0] + cnt, ts)
        raise


async def _write_audit(
    actor_id: int,
    action: str,
    scope: str,
    keyword: str,
    guild_id: int | None = None,
    old_keyword: str | None = None,
    old_replacement: str | None = None,
    new_replacement: str | None = None,
):
    """단일 트랜잭션에 audit row를 INSERT. 호출자가 commit 책임. None이면 audit 미기록."""
    if actor_id is None:
        return
    await _db.execute(
        """
        INSERT INTO pronunciation_audit
            (actor_id, action, scope, guild_id, keyword, old_keyword, old_replacement, new_replacement)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (actor_id, action, scope, guild_id, keyword, old_keyword, old_replacement, new_replacement),
    )


async def get_audit_log(limit: int = 100) -> list[dict]:
    async with _db.execute(
        """
        SELECT id, timestamp, actor_id, action, scope, guild_id, keyword,
               old_keyword, old_replacement, new_replacement
        FROM pronunciation_audit
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ) as cursor:
        return [
            {
                "id": row[0],
                "timestamp": row[1],
                "actor_id": row[2],
                "action": row[3],
                "scope": row[4],
                "guild_id": row[5],
                "keyword": row[6],
                "old_keyword": row[7],
                "old_replacement": row[8],
                "new_replacement": row[9],
            }
            async for row in cursor
        ]


async def get_global_keyword_aliases() -> list[dict]:
    async with _db.execute(
        """
        SELECT keyword, replacement, hit_count, last_seen_at, created_at
        FROM global_keyword_aliases
        ORDER BY created_at ASC, keyword ASC
        """
    ) as cursor:
        return [
            {
                "keyword": row[0],
                "replacement": row[1],
                "hit_count": row[2] or 0,
                "last_seen_at": row[3],
                "created_at": row[4],
            }
            async for row in cursor
        ]


async def add_global_keyword_alias(
    keyword: str, replacement: str, *, audit_actor: int | None = None,
) -> bool:
    try:
        await _db.execute(
            "INSERT INTO global_keyword_aliases (keyword, replacement) VALUES (?, ?)",
            (keyword, replacement),
        )
    except aiosqlite.IntegrityError:
        await _db.rollback()
        return False
    if audit_actor is not None:
        await _write_audit(
            actor_id=audit_actor, action="add", scope="global",
            keyword=keyword, new_replacement=replacement,
        )
    await _db.commit()
    _global_keyword_cache[keyword] = replacement
    return True


async def remove_global_keyword_alias(
    keyword: str, *, audit_actor: int | None = None,
) -> bool:
    async with _db.execute(
        "SELECT replacement FROM global_keyword_aliases WHERE keyword = ?",
        (keyword,),
    ) as cursor:
        existing = await cursor.fetchone()
    if existing is None:
        return False

    await _db.execute(
        "DELETE FROM global_keyword_aliases WHERE keyword = ?",
        (keyword,),
    )
    if audit_actor is not None:
        await _write_audit(
            actor_id=audit_actor, action="delete", scope="global",
            keyword=keyword, old_replacement=existing[0],
        )
    await _db.commit()
    _global_keyword_cache.pop(keyword, None)
    return True


async def update_global_keyword_alias(
    original_keyword: str, keyword: str, replacement: str,
    *, audit_actor: int | None = None,
) -> str:
    async with _db.execute(
        "SELECT keyword, replacement FROM global_keyword_aliases WHERE keyword = ?",
        (original_keyword,),
    ) as cursor:
        existing = await cursor.fetchone()
    if existing is None:
        return "not_found"
    old_kw, old_repl = existing[0], existing[1]

    try:
        await _db.execute(
            """
            UPDATE global_keyword_aliases
            SET keyword = ?, replacement = ?
            WHERE keyword = ?
            """,
            (keyword, replacement, original_keyword),
        )
    except aiosqlite.IntegrityError:
        await _db.rollback()
        return "conflict"

    if audit_actor is not None:
        await _write_audit(
            actor_id=audit_actor, action="update", scope="global",
            keyword=keyword,
            old_keyword=old_kw if old_kw != keyword else None,
            old_replacement=old_repl,
            new_replacement=replacement,
        )
    await _db.commit()

    if original_keyword != keyword:
        _global_keyword_cache.pop(original_keyword, None)
    _global_keyword_cache[keyword] = replacement
    return "updated"


async def get_guild_keyword_aliases() -> list[dict]:
    async with _db.execute(
        """
        SELECT guild_id, keyword, replacement, hit_count, last_seen_at, created_at
        FROM guild_keyword_aliases
        ORDER BY guild_id ASC, created_at ASC, keyword ASC
        """
    ) as cursor:
        return [
            {
                "guild_id": row[0],
                "keyword": row[1],
                "replacement": row[2],
                "hit_count": row[3] or 0,
                "last_seen_at": row[4],
                "created_at": row[5],
            }
            async for row in cursor
        ]


async def get_guild_keyword_aliases_for(guild_id: int) -> list[dict]:
    async with _db.execute(
        """
        SELECT keyword, replacement, hit_count, last_seen_at, created_at
        FROM guild_keyword_aliases
        WHERE guild_id = ?
        ORDER BY created_at ASC, keyword ASC
        """,
        (guild_id,),
    ) as cursor:
        return [
            {
                "keyword": row[0],
                "replacement": row[1],
                "hit_count": row[2] or 0,
                "last_seen_at": row[3],
                "created_at": row[4],
            }
            async for row in cursor
        ]


async def add_guild_keyword_alias(
    guild_id: int, keyword: str, replacement: str, *, audit_actor: int | None = None,
) -> bool:
    try:
        await _db.execute(
            "INSERT INTO guild_keyword_aliases (guild_id, keyword, replacement) VALUES (?, ?, ?)",
            (guild_id, keyword, replacement),
        )
    except aiosqlite.IntegrityError:
        await _db.rollback()
        return False
    if audit_actor is not None:
        await _write_audit(
            actor_id=audit_actor, action="add", scope="guild",
            guild_id=guild_id, keyword=keyword, new_replacement=replacement,
        )
    await _db.commit()
    _guild_keyword_cache.setdefault(guild_id, {})[keyword] = replacement
    return True


async def remove_guild_keyword_alias(
    guild_id: int, keyword: str, *, audit_actor: int | None = None,
) -> bool:
    async with _db.execute(
        "SELECT replacement FROM guild_keyword_aliases WHERE guild_id = ? AND keyword = ?",
        (guild_id, keyword),
    ) as cursor:
        existing = await cursor.fetchone()
    if existing is None:
        return False

    await _db.execute(
        "DELETE FROM guild_keyword_aliases WHERE guild_id = ? AND keyword = ?",
        (guild_id, keyword),
    )
    if audit_actor is not None:
        await _write_audit(
            actor_id=audit_actor, action="delete", scope="guild",
            guild_id=guild_id, keyword=keyword, old_replacement=existing[0],
        )
    await _db.commit()

    guild_aliases = _guild_keyword_cache.get(guild_id, {})
    guild_aliases.pop(keyword, None)
    if not guild_aliases and guild_id in _guild_keyword_cache:
        _guild_keyword_cache.pop(guild_id, None)
    return True


async def update_guild_keyword_alias(
    guild_id: int, original_keyword: str, keyword: str, replacement: str,
    *, audit_actor: int | None = None,
) -> str:
    async with _db.execute(
        "SELECT keyword, replacement FROM guild_keyword_aliases WHERE guild_id = ? AND keyword = ?",
        (guild_id, original_keyword),
    ) as cursor:
        existing = await cursor.fetchone()
    if existing is None:
        return "not_found"
    old_kw, old_repl = existing[0], existing[1]

    try:
        await _db.execute(
            """
            UPDATE guild_keyword_aliases
            SET keyword = ?, replacement = ?
            WHERE guild_id = ? AND keyword = ?
            """,
            (keyword, replacement, guild_id, original_keyword),
        )
    except aiosqlite.IntegrityError:
        await _db.rollback()
        return "conflict"

    if audit_actor is not None:
        await _write_audit(
            actor_id=audit_actor, action="update", scope="guild",
            guild_id=guild_id, keyword=keyword,
            old_keyword=old_kw if old_kw != keyword else None,
            old_replacement=old_repl,
            new_replacement=replacement,
        )
    await _db.commit()

    guild_aliases = _guild_keyword_cache.setdefault(guild_id, {})
    if original_keyword != keyword:
        guild_aliases.pop(original_keyword, None)
    guild_aliases[keyword] = replacement
    return "updated"


async def import_keyword_aliases_batch(
    rows: list[dict], actor_id: int,
) -> tuple[int, int]:
    """CSV 일괄 import. 단일 트랜잭션으로 처리. 반환: (added, skipped)."""
    added = 0
    skipped = 0
    for row in rows:
        scope = row.get("scope")
        keyword = row.get("keyword")
        replacement = row.get("replacement")
        guild_id = row.get("guild_id")
        try:
            if scope == "global":
                await _db.execute(
                    "INSERT INTO global_keyword_aliases (keyword, replacement) VALUES (?, ?)",
                    (keyword, replacement),
                )
                await _write_audit(
                    actor_id=actor_id, action="add", scope="global",
                    keyword=keyword, new_replacement=replacement,
                )
                _global_keyword_cache[keyword] = replacement
                added += 1
            elif scope == "guild":
                await _db.execute(
                    "INSERT INTO guild_keyword_aliases (guild_id, keyword, replacement) VALUES (?, ?, ?)",
                    (guild_id, keyword, replacement),
                )
                await _write_audit(
                    actor_id=actor_id, action="add", scope="guild",
                    guild_id=guild_id, keyword=keyword, new_replacement=replacement,
                )
                _guild_keyword_cache.setdefault(guild_id, {})[keyword] = replacement
                added += 1
            else:
                skipped += 1
        except aiosqlite.IntegrityError:
            skipped += 1
    await _db.commit()
    return added, skipped


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
        "SELECT engine, voice, speed, lang, total_steps FROM user_settings WHERE user_id = ?",
        (user_id,),
    ) as cursor:
        row = await cursor.fetchone()
        if row:
            return {
                "engine": row[0], "voice": row[1],
                "speed": row[2], "lang": row[3], "total_steps": row[4],
            }
    return dict(DEFAULT_USER_SETTINGS)


async def set_user_setting(user_id: int, **kwargs):
    current = await get_user_settings(user_id)
    current.update(kwargs)
    await _db.execute(
        """INSERT INTO user_settings (user_id, engine, voice, speed, lang, total_steps)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               engine=excluded.engine, voice=excluded.voice, speed=excluded.speed,
               lang=excluded.lang, total_steps=excluded.total_steps""",
        (user_id, current["engine"], current["voice"], current["speed"],
         current["lang"], current["total_steps"]),
    )
    await _db.commit()


# ── Google TTS 글자수 사용량 ──

async def increment_tts_char_usage(voice_type: str, char_count: int):
    from datetime import datetime
    month = datetime.now(KST).strftime("%Y-%m")
    await _db.execute(
        """INSERT INTO tts_char_usage (voice_type, month, char_count)
           VALUES (?, ?, ?)
           ON CONFLICT(voice_type, month) DO UPDATE SET
               char_count = char_count + excluded.char_count""",
        (voice_type, month, char_count),
    )
    await _db.commit()


async def get_tts_char_usage(month: str | None = None) -> dict[str, int]:
    from datetime import datetime
    if month is None:
        month = datetime.now(KST).strftime("%Y-%m")
    async with _db.execute(
        "SELECT voice_type, char_count FROM tts_char_usage WHERE month = ?",
        (month,),
    ) as cursor:
        return {row[0]: row[1] async for row in cursor}
