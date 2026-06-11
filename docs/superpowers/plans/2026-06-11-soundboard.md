# 사운드보드 기능 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 8초 이하 음원을 키워드와 함께 등록(슬래시 명령어 + 대시보드)하고 `/play <키워드>`로 음성 채널에서 재생하는 사운드보드 기능.

**Architecture:** 업로드 시 ffprobe로 검증(오디오 스트림 + 8초 이하) 후 ffmpeg으로 오디오만 추출해 `data/sounds/<bot_id>/<uuid>.ogg`에 저장. DB(`sounds` 테이블)에는 메타데이터만 기록. 재생은 기존 TTS와 동일한 길드별 락을 공유. 범위는 전역/길드 2계층(길드 우선), 모든 데이터는 `bot_id` 스코프.

**Tech Stack:** Python 3.11+, discord.py(app_commands), aiosqlite, Quart, ffmpeg/ffprobe(subprocess), pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-06-11-soundboard-design.md`

---

## 테스트 실행 환경 (Windows 로컬)

Docker 밖 Windows에서 테스트를 돌릴 때는 매번 다음과 같이 실행한다 (tzdata는 venv에 이미 설치됨):

```powershell
$env:PYTHONUTF8='1'; $env:DISCORD_TOKEN='test-token'; .\.venv\Scripts\python.exe -m pytest tests/ -q
```

- `PYTHONUTF8=1`: 기존 테스트 일부가 `read_text()`를 인코딩 없이 호출 (cp949 회피)
- `DISCORD_TOKEN`: `config.py`가 import 시점에 요구

## 파일 구조

| 파일 | 작업 | 책임 |
|---|---|---|
| `config.py` | 수정 | 사운드보드 상수 (길이/크기/키워드/개수 제한, 저장 경로) |
| `database.py` | 수정 | `sounds` 테이블 + CRUD (add/resolve/list/remove/count/play_count) |
| `sound_storage.py` | 생성 | ffprobe 검증 + ffmpeg 변환 + 파일 저장/삭제 (업로드 공통 파이프라인) |
| `tts_engine.py` | 수정 | `_ensure_voice_client` 헬퍼 추출 + `play_sound` 추가 |
| `cogs/sounds.py` | 생성 | `/sound add·remove·list`, `/play` 슬래시 명령어 |
| `bot.py` | 수정 | `EXTENSIONS`에 `cogs.sounds` 추가 |
| `web/routes.py` | 수정 | sounds 섹션 데이터 + 업로드/삭제 라우트 |
| `web/templates/dashboard.html` | 수정 | 사운드보드 nav + 섹션 (목록 + 업로드 폼) |
| `tests/test_sounds.py` | 생성 | DB CRUD/우선순위/스코프 + sound_storage 검증 테스트 |
| `tests/test_tts_pipeline.py` | 수정 | 깨진 FakeVoiceClient 수정 + `play_sound` 테스트 |
| `tests/test_dashboard_multibot_ux.py` | 수정 | sounds 섹션 string-assertion 테스트 |
| `README.md`, `README.ko.md` | 수정 | 기능/명령어 문서화 |

---

### Task 0: 기존 테스트 수리 (사전 정리)

HEAD에서 이미 깨진 테스트 1개와 Windows 인코딩 문제를 고친다.

**Files:**
- Modify: `tests/test_tts_pipeline.py` (FakeVoiceClient에 `is_connected` 추가)
- Modify: `tests/test_dashboard_multibot_ux.py` (`read_text()`에 `encoding="utf-8"`)

- [ ] **Step 1: 실패 확인**

Run: `$env:PYTHONUTF8='1'; $env:DISCORD_TOKEN='test-token'; .\.venv\Scripts\python.exe -m pytest tests/test_tts_pipeline.py -q`
Expected: 1 failed — `assert "TTS 오류: 'FakeVoiceClient' object has no attribute 'is_connected'" is None`

- [ ] **Step 2: FakeVoiceClient 수정**

`tests/test_tts_pipeline.py`의 `FakeVoiceClient` 클래스(`test_do_tts_applies_keyword_replacement_before_engine_synthesis` 안)에 메서드 추가:

```python
    class FakeVoiceClient:
        channel = object()

        def is_connected(self):
            return True

        def is_playing(self):
            return False

        def play(self, audio):
            calls.append(("play", audio))
```

- [ ] **Step 3: read_text 인코딩 명시**

`tests/test_dashboard_multibot_ux.py`에서 4곳 모두 치환:
- `ROUTES.read_text()` → `ROUTES.read_text(encoding="utf-8")`
- `TEMPLATE.read_text()` → `TEMPLATE.read_text(encoding="utf-8")`

- [ ] **Step 4: 전체 테스트 통과 확인**

Run: `$env:PYTHONUTF8='1'; $env:DISCORD_TOKEN='test-token'; .\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: 16 passed

- [ ] **Step 5: Commit**

```powershell
git add tests/test_tts_pipeline.py tests/test_dashboard_multibot_ux.py
git commit -m "test: fix stale FakeVoiceClient and cp949 read_text portability"
```

---

### Task 1: config 상수 + sounds DB 테이블/CRUD

**Files:**
- Modify: `config.py` (파일 끝에 상수 추가)
- Modify: `database.py` (`_create_multibot_tables`의 `table_sql` dict + 파일 끝 CRUD)
- Test: `tests/test_sounds.py` (생성)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_sounds.py` 생성:

```python
import importlib
import os
import sys
import types

import pytest
import pytest_asyncio

os.environ.setdefault("DISCORD_TOKEN", "test-token")


class _DummyLogger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


sys.modules.setdefault("loguru", types.SimpleNamespace(logger=_DummyLogger()))


@pytest_asyncio.fixture()
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "bot.db"))
    monkeypatch.setenv("DISCORD_TOKEN", "default-token")
    import database
    import config

    importlib.reload(config)
    database = importlib.reload(database)
    await database.init_db()
    try:
        yield database
    finally:
        await database.close_db()


@pytest.mark.asyncio
async def test_add_and_resolve_sound_guild_overrides_global(db):
    created = await db.add_sound("global", "ㅋㅋㅋ", "global.ogg", 3.0, bot_id=1)
    assert created["id"] > 0
    assert await db.add_sound("guild", "ㅋㅋㅋ", "guild.ogg", 2.0, guild_id=10, bot_id=1)

    resolved = await db.resolve_sound("ㅋㅋㅋ", guild_id=10, bot_id=1)
    assert resolved["filename"] == "guild.ogg"
    assert resolved["scope"] == "guild"

    other_guild = await db.resolve_sound("ㅋㅋㅋ", guild_id=99, bot_id=1)
    assert other_guild["filename"] == "global.ogg"

    assert await db.resolve_sound("없는키워드", guild_id=10, bot_id=1) is None


@pytest.mark.asyncio
async def test_sounds_are_scoped_by_bot_id(db):
    await db.add_sound("global", "hi", "a.ogg", 1.0, bot_id=1)
    assert await db.resolve_sound("hi", bot_id=2) is None
    assert (await db.resolve_sound("hi", bot_id=1))["filename"] == "a.ogg"


@pytest.mark.asyncio
async def test_duplicate_sound_keyword_rejected_per_scope(db):
    assert await db.add_sound("global", "dup", "a.ogg", 1.0, bot_id=1)
    assert await db.add_sound("global", "dup", "b.ogg", 1.0, bot_id=1) is None
    # 같은 키워드라도 길드 스코프는 별개로 등록 가능
    assert await db.add_sound("guild", "dup", "c.ogg", 1.0, guild_id=10, bot_id=1)
    assert await db.add_sound("guild", "dup", "d.ogg", 1.0, guild_id=10, bot_id=1) is None
    # guild 스코프인데 guild_id가 없으면 거부
    assert await db.add_sound("guild", "x", "e.ogg", 1.0, bot_id=1) is None


@pytest.mark.asyncio
async def test_remove_sound_returns_row_and_counts(db):
    await db.add_sound("guild", "x", "x.ogg", 1.0, guild_id=10, bot_id=1)
    assert await db.get_guild_sound_count(10, bot_id=1) == 1
    removed = await db.remove_sound("guild", "x", guild_id=10, bot_id=1)
    assert removed["filename"] == "x.ogg"
    assert await db.get_guild_sound_count(10, bot_id=1) == 0
    assert await db.remove_sound("guild", "x", guild_id=10, bot_id=1) is None


@pytest.mark.asyncio
async def test_get_sounds_for_guild_merges_guild_and_global(db):
    await db.add_sound("global", "a", "ga.ogg", 1.0, bot_id=1)
    await db.add_sound("global", "b", "gb.ogg", 1.0, bot_id=1)
    await db.add_sound("guild", "a", "la.ogg", 1.0, guild_id=10, bot_id=1)

    sounds = await db.get_sounds_for_guild(10, bot_id=1)
    by_keyword = {s["keyword"]: s for s in sounds}
    assert len(sounds) == 2
    assert by_keyword["a"]["filename"] == "la.ogg"  # 길드가 전역을 가림
    assert by_keyword["b"]["filename"] == "gb.ogg"


@pytest.mark.asyncio
async def test_play_count_increment_and_remove_by_id(db):
    created = await db.add_sound("global", "pc", "pc.ogg", 1.0, bot_id=1)
    await db.increment_sound_play_count(created["id"])
    await db.increment_sound_play_count(created["id"])
    assert (await db.get_sound_by_id(created["id"]))["play_count"] == 2
    removed = await db.remove_sound_by_id(created["id"])
    assert removed["keyword"] == "pc"
    assert await db.get_sound_by_id(created["id"]) is None
```

- [ ] **Step 2: 실패 확인**

Run: `$env:PYTHONUTF8='1'; $env:DISCORD_TOKEN='test-token'; .\.venv\Scripts\python.exe -m pytest tests/test_sounds.py -q`
Expected: 6 failed — `AttributeError: module 'database' has no attribute 'add_sound'`

- [ ] **Step 3: config.py 상수 추가**

`config.py` 파일 끝(`DEFAULT_USER_SETTINGS` 뒤)에 추가:

```python
# 사운드보드
SOUND_MAX_DURATION_SECONDS = 8.0
SOUND_MAX_FILE_BYTES = 20 * 1024 * 1024
SOUND_MAX_KEYWORD_LENGTH = 50
SOUND_MAX_PER_GUILD = 100
SOUNDS_DIR = os.getenv("SOUNDS_DIR", "data/sounds")
```

- [ ] **Step 4: sounds 테이블 추가**

`database.py`의 `_create_multibot_tables()` 안 `table_sql` dict에 항목 추가 (`"pronunciation_audit_v2"` 항목 뒤):

```python
        "sounds": """
            CREATE TABLE IF NOT EXISTS sounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id INTEGER NOT NULL DEFAULT 1,
                scope TEXT NOT NULL CHECK(scope IN ('global', 'guild')), guild_id INTEGER,
                keyword TEXT NOT NULL, filename TEXT NOT NULL, duration_seconds REAL NOT NULL,
                original_filename TEXT, created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                play_count INTEGER NOT NULL DEFAULT 0)
        """,
```

그리고 같은 함수의 마지막 줄(`CREATE INDEX IF NOT EXISTS idx_audit_v2_timestamp ...` 실행 다음)에 추가:

```python
    # SQLite UNIQUE 제약은 NULL을 서로 다른 값으로 취급하므로 (전역 음원 guild_id=NULL 중복 방지)
    # COALESCE 식 인덱스로 유니크를 강제한다
    await _db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sounds_unique ON sounds (bot_id, scope, COALESCE(guild_id, 0), keyword)"
    )
```

- [ ] **Step 5: CRUD 함수 추가**

`database.py` 파일 끝에 추가:

```python
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
    bid = _bot_id(bot_id)
    if scope == "guild":
        if guild_id is None:
            return None
        sql = f"SELECT {_SOUND_COLS} FROM sounds WHERE bot_id = ? AND scope = 'guild' AND guild_id = ? AND keyword = ?"
        params: tuple = (bid, guild_id, keyword)
    else:
        sql = f"SELECT {_SOUND_COLS} FROM sounds WHERE bot_id = ? AND scope = 'global' AND keyword = ?"
        params = (bid, keyword)
    async with _db.execute(sql, params) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    await _db.execute("DELETE FROM sounds WHERE id = ?", (row[0],))
    await _db.commit()
    return _sound_row_to_dict(row)


async def remove_sound_by_id(sound_id: int) -> dict | None:
    sound = await get_sound_by_id(sound_id)
    if sound is None:
        return None
    await _db.execute("DELETE FROM sounds WHERE id = ?", (int(sound_id),))
    await _db.commit()
    return sound


async def increment_sound_play_count(sound_id: int):
    await _db.execute("UPDATE sounds SET play_count = play_count + 1 WHERE id = ?", (int(sound_id),))
    await _db.commit()
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `$env:PYTHONUTF8='1'; $env:DISCORD_TOKEN='test-token'; .\.venv\Scripts\python.exe -m pytest tests/test_sounds.py -q`
Expected: 6 passed

- [ ] **Step 7: 전체 테스트 + Commit**

Run: `$env:PYTHONUTF8='1'; $env:DISCORD_TOKEN='test-token'; .\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: 22 passed

```powershell
git add config.py database.py tests/test_sounds.py
git commit -m "feat: add sounds table and CRUD for soundboard"
```

---

### Task 2: sound_storage 모듈 (ffprobe 검증 + ffmpeg 변환)

**Files:**
- Create: `sound_storage.py`
- Test: `tests/test_sounds.py` (테스트 추가)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_sounds.py` 끝에 추가:

```python
# ───────────────────────── sound_storage ─────────────────────────

import json
from pathlib import Path


def _load_sound_storage():
    import sound_storage

    return importlib.reload(sound_storage)


def test_parse_probe_output_extracts_duration():
    sound_storage = _load_sound_storage()
    raw = json.dumps({"streams": [{"codec_type": "audio"}], "format": {"duration": "3.25"}})
    assert sound_storage.parse_probe_output(raw) == 3.25


def test_parse_probe_output_returns_none_when_invalid():
    sound_storage = _load_sound_storage()
    no_audio = json.dumps({"streams": [{"codec_type": "video"}], "format": {"duration": "3.0"}})
    no_duration = json.dumps({"streams": [{"codec_type": "audio"}], "format": {}})
    assert sound_storage.parse_probe_output(no_audio) is None
    assert sound_storage.parse_probe_output(no_duration) is None
    assert sound_storage.parse_probe_output("not-json") is None


@pytest.mark.asyncio
async def test_save_sound_file_rejects_oversize(monkeypatch, tmp_path):
    sound_storage = _load_sound_storage()
    monkeypatch.setattr(sound_storage, "SOUNDS_DIR", str(tmp_path / "sounds"))
    monkeypatch.setattr(sound_storage, "SOUND_MAX_FILE_BYTES", 10)

    with pytest.raises(sound_storage.SoundValidationError, match="너무 큽니다"):
        await sound_storage.save_sound_file(b"x" * 11, bot_id=1)


@pytest.mark.asyncio
async def test_save_sound_file_rejects_long_audio(monkeypatch, tmp_path):
    sound_storage = _load_sound_storage()
    monkeypatch.setattr(sound_storage, "SOUNDS_DIR", str(tmp_path / "sounds"))

    async def fake_run(*args):
        payload = {"streams": [{"codec_type": "audio"}], "format": {"duration": "8.5"}}
        return 0, json.dumps(payload).encode(), b""

    monkeypatch.setattr(sound_storage, "_run", fake_run)

    with pytest.raises(sound_storage.SoundValidationError, match="8초 이하"):
        await sound_storage.save_sound_file(b"fake", bot_id=1)
    assert not (tmp_path / "sounds").exists()  # 거부된 업로드는 흔적을 남기지 않음


@pytest.mark.asyncio
async def test_save_sound_file_rejects_no_audio_stream(monkeypatch, tmp_path):
    sound_storage = _load_sound_storage()
    monkeypatch.setattr(sound_storage, "SOUNDS_DIR", str(tmp_path / "sounds"))

    async def fake_run(*args):
        payload = {"streams": [{"codec_type": "video"}], "format": {"duration": "3.0"}}
        return 0, json.dumps(payload).encode(), b""

    monkeypatch.setattr(sound_storage, "_run", fake_run)

    with pytest.raises(sound_storage.SoundValidationError, match="오디오 트랙"):
        await sound_storage.save_sound_file(b"fake", bot_id=1)


@pytest.mark.asyncio
async def test_save_sound_file_converts_and_stores(monkeypatch, tmp_path):
    sound_storage = _load_sound_storage()
    monkeypatch.setattr(sound_storage, "SOUNDS_DIR", str(tmp_path / "sounds"))

    async def fake_run(*args):
        if args[0] == "ffprobe":
            payload = {"streams": [{"codec_type": "audio"}], "format": {"duration": "4.2"}}
            return 0, json.dumps(payload).encode(), b""
        if args[0] == "ffmpeg":
            Path(args[-1]).write_bytes(b"fake-ogg")
            return 0, b"", b""
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(sound_storage, "_run", fake_run)

    filename, duration = await sound_storage.save_sound_file(b"fake-mp4", bot_id=1)
    assert duration == 4.2
    assert filename.endswith(".ogg")
    stored = sound_storage.sound_path(filename, bot_id=1)
    assert stored.read_bytes() == b"fake-ogg"

    sound_storage.delete_sound_file(filename, bot_id=1)
    assert not stored.exists()
    # 이미 없는 파일 삭제는 조용히 무시
    sound_storage.delete_sound_file(filename, bot_id=1)


@pytest.mark.asyncio
async def test_save_sound_file_raises_on_ffmpeg_failure(monkeypatch, tmp_path):
    sound_storage = _load_sound_storage()
    monkeypatch.setattr(sound_storage, "SOUNDS_DIR", str(tmp_path / "sounds"))

    async def fake_run(*args):
        if args[0] == "ffprobe":
            payload = {"streams": [{"codec_type": "audio"}], "format": {"duration": "2.0"}}
            return 0, json.dumps(payload).encode(), b""
        return 1, b"", b"conversion error"

    monkeypatch.setattr(sound_storage, "_run", fake_run)

    with pytest.raises(sound_storage.SoundValidationError, match="변환에 실패"):
        await sound_storage.save_sound_file(b"fake", bot_id=1)
```

- [ ] **Step 2: 실패 확인**

Run: `$env:PYTHONUTF8='1'; $env:DISCORD_TOKEN='test-token'; .\.venv\Scripts\python.exe -m pytest tests/test_sounds.py -q`
Expected: 7 failed — `ModuleNotFoundError: No module named 'sound_storage'`

- [ ] **Step 3: sound_storage.py 작성**

`sound_storage.py` 생성:

```python
"""사운드보드 음원 파일 저장/검증 파이프라인.

슬래시 명령어와 대시보드가 공유하는 업로드 공통 로직:
크기 검증 → ffprobe(오디오 스트림 + 길이) → ffmpeg(오디오만 추출, opus 변환)
→ data/sounds/<bot_id>/<uuid>.ogg 저장
"""

import asyncio
import json
import os
import tempfile
import uuid
from contextlib import suppress
from pathlib import Path

from loguru import logger as log

from config import SOUND_MAX_DURATION_SECONDS, SOUND_MAX_FILE_BYTES, SOUNDS_DIR


class SoundValidationError(Exception):
    """사용자에게 그대로 안내 가능한 검증 실패 메시지."""


def sound_path(filename: str, *, bot_id: int) -> Path:
    return Path(SOUNDS_DIR) / str(int(bot_id)) / filename


async def _run(*args: str) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout, stderr


def parse_probe_output(raw: str) -> float | None:
    """ffprobe JSON 출력에서 오디오 길이(초)를 꺼낸다. 오디오 스트림이 없으면 None."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    streams = data.get("streams") or []
    if not any(s.get("codec_type") == "audio" for s in streams):
        return None
    try:
        return float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        return None


async def probe_audio_duration(path: str) -> float | None:
    rc, stdout, _stderr = await _run(
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type",
        "-show_entries", "format=duration",
        "-of", "json", path,
    )
    if rc != 0:
        return None
    return parse_probe_output(stdout.decode("utf-8", errors="replace"))


async def save_sound_file(data: bytes, *, bot_id: int) -> tuple[str, float]:
    """업로드 데이터를 검증·변환해 저장하고 (파일명, 길이초)를 돌려준다.

    검증 실패 시 SoundValidationError.
    """
    if len(data) > SOUND_MAX_FILE_BYTES:
        mb = SOUND_MAX_FILE_BYTES // (1024 * 1024)
        raise SoundValidationError(f"파일이 너무 큽니다. (최대 {mb}MB)")

    src = tempfile.NamedTemporaryFile(delete=False)
    try:
        src.write(data)
        src.close()

        duration = await probe_audio_duration(src.name)
        if duration is None:
            raise SoundValidationError("오디오 트랙을 찾을 수 없는 파일입니다.")
        if duration > SOUND_MAX_DURATION_SECONDS:
            raise SoundValidationError(
                f"음원 길이는 {SOUND_MAX_DURATION_SECONDS:.0f}초 이하여야 합니다. (현재 {duration:.1f}초)"
            )

        dest_dir = Path(SOUNDS_DIR) / str(int(bot_id))
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.ogg"
        dest = dest_dir / filename

        rc, _stdout, stderr = await _run(
            "ffmpeg", "-y", "-i", src.name,
            "-vn", "-ac", "2", "-ar", "48000", "-c:a", "libopus", "-b:a", "96k",
            str(dest),
        )
        if rc != 0:
            log.warning("ffmpeg 변환 실패 rc={} stderr={}", rc, stderr[-500:])
            with suppress(OSError):
                dest.unlink(missing_ok=True)
            raise SoundValidationError("오디오 변환에 실패했습니다. 파일 형식을 확인해주세요.")
        return filename, duration
    finally:
        with suppress(OSError):
            os.unlink(src.name)


def delete_sound_file(filename: str, *, bot_id: int) -> None:
    path = sound_path(filename, bot_id=bot_id)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("음원 파일 삭제 실패 {}: {}", path, exc)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `$env:PYTHONUTF8='1'; $env:DISCORD_TOKEN='test-token'; .\.venv\Scripts\python.exe -m pytest tests/test_sounds.py -q`
Expected: 13 passed

- [ ] **Step 5: Commit**

```powershell
git add sound_storage.py tests/test_sounds.py
git commit -m "feat: add sound_storage upload pipeline (ffprobe validation + ffmpeg opus conversion)"
```

---

### Task 3: tts_engine 리팩토링 — 음성 클라이언트 헬퍼 + play_sound

**Files:**
- Modify: `tts_engine.py:84-94` (연결 로직을 헬퍼로 추출), 파일 끝에 `play_sound` 추가
- Test: `tests/test_tts_pipeline.py` (테스트 추가)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_tts_pipeline.py` 끝에 추가:

```python
@pytest.mark.asyncio
async def test_play_sound_plays_file_without_deleting_it(tmp_path):
    tts_engine = reload_tts_engine()

    calls = []

    class FakeVoiceClient:
        channel = object()

        def is_connected(self):
            return True

        def is_playing(self):
            return False

        def play(self, audio):
            calls.append(("play", audio))

    guild = types.SimpleNamespace(id=888, voice_client=FakeVoiceClient())
    sound_file = tmp_path / "sound.ogg"
    sound_file.write_bytes(b"ogg")

    error = await tts_engine.play_sound(str(sound_file), guild.voice_client.channel, guild)

    assert error is None
    assert calls == [("play", str(sound_file))]
    assert sound_file.exists()  # TTS와 달리 재생 후 파일을 지우지 않는다
```

- [ ] **Step 2: 실패 확인**

Run: `$env:PYTHONUTF8='1'; $env:DISCORD_TOKEN='test-token'; .\.venv\Scripts\python.exe -m pytest tests/test_tts_pipeline.py -q`
Expected: 1 failed — `AttributeError: module 'tts_engine' has no attribute 'play_sound'`

- [ ] **Step 3: 헬퍼 추출 + play_sound 구현**

`tts_engine.py`의 `do_tts` 안 연결 블록(현재 84-94행):

```python
            vc = guild.voice_client
            if vc is None or not vc.is_connected():
                if vc is not None:
                    try:
                        await vc.disconnect(force=True)
                    except Exception:
                        pass
                vc = await voice_channel.connect()
            elif vc.channel != voice_channel:
                await vc.move_to(voice_channel)
```

이 블록을 한 줄로 교체:

```python
            vc = await _ensure_voice_client(guild, voice_channel)
```

그리고 `do_tts` 함수 **앞**(`apply_keyword_replacement` 함수 뒤)에 헬퍼 추가:

```python
async def _ensure_voice_client(guild: discord.Guild, voice_channel: discord.VoiceChannel):
    """음성 클라이언트를 voice_channel에 연결된 상태로 만든다 (stale이면 강제 재연결)."""
    vc = guild.voice_client
    if vc is None or not vc.is_connected():
        if vc is not None:
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
        vc = await voice_channel.connect()
    elif vc.channel != voice_channel:
        await vc.move_to(voice_channel)
    return vc
```

마지막으로 `tts_engine.py` 파일 끝에 추가:

```python
async def play_sound(
    file_path: str,
    voice_channel: discord.VoiceChannel,
    guild: discord.Guild,
) -> str | None:
    """사운드보드 음원 파일을 재생한다. TTS와 같은 길드 락을 공유해 순서를 보장한다."""
    async with _locks[guild.id]:
        try:
            vc = await _ensure_voice_client(guild, voice_channel)
            if vc.is_playing():
                vc.stop()

            vc.play(discord.FFmpegPCMAudio(file_path))

            while vc.is_playing():
                await asyncio.sleep(0.5)

            return None
        except Exception as e:
            return f"사운드 재생 오류: {str(e)}"
```

- [ ] **Step 4: 테스트 통과 확인 (기존 do_tts 테스트 포함)**

Run: `$env:PYTHONUTF8='1'; $env:DISCORD_TOKEN='test-token'; .\.venv\Scripts\python.exe -m pytest tests/test_tts_pipeline.py tests/test_sounds.py -q`
Expected: 20 passed (test_tts_pipeline 7 + test_sounds 13)

- [ ] **Step 5: Commit**

```powershell
git add tts_engine.py tests/test_tts_pipeline.py
git commit -m "refactor: extract voice client helper and add play_sound for soundboard"
```

---

### Task 4: cogs/sounds.py 슬래시 명령어 + EXTENSIONS 등록

**Files:**
- Create: `cogs/sounds.py`
- Modify: `bot.py:38` (`EXTENSIONS`)

- [ ] **Step 1: cogs/sounds.py 작성**

```python
import discord
from discord import app_commands
from discord.ext import commands
from loguru import logger as log

import database
import sound_storage
import tts_engine
from config import SOUND_MAX_KEYWORD_LENGTH, SOUND_MAX_PER_GUILD


class SoundCog(commands.Cog):
    """사운드보드: 키워드로 짧은 음원(8초 이하)을 등록하고 /play로 재생."""

    sound_group = app_commands.Group(name="sound", description="사운드보드 음원 관리")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _keyword_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        sounds = await database.get_sounds_for_guild(interaction.guild.id, bot_id=self.bot.bot_id)
        query = current.lower()
        return [
            app_commands.Choice(name=f"{s['keyword']} ({s['duration_seconds']:.1f}초)", value=s["keyword"])
            for s in sounds
            if not query or query in s["keyword"].lower()
        ][:25]

    @sound_group.command(name="add", description="키워드에 음원을 등록합니다 (8초 이하)")
    @app_commands.describe(keyword="재생에 사용할 키워드", file="8초 이하의 오디오/비디오 파일 (mp4, mp3 등)")
    async def cmd_sound_add(self, interaction: discord.Interaction, keyword: str, file: discord.Attachment):
        if interaction.guild is None:
            await interaction.response.send_message("❌ 서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return
        keyword = keyword.strip()
        if not keyword or len(keyword) > SOUND_MAX_KEYWORD_LENGTH:
            await interaction.response.send_message(
                f"❌ 키워드는 1~{SOUND_MAX_KEYWORD_LENGTH}자여야 합니다.", ephemeral=True,
            )
            return
        if await database.get_guild_sound_count(interaction.guild.id, bot_id=self.bot.bot_id) >= SOUND_MAX_PER_GUILD:
            await interaction.response.send_message(
                f"❌ 서버당 음원은 최대 {SOUND_MAX_PER_GUILD}개까지 등록할 수 있습니다.", ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            data = await file.read()
            filename, duration = await sound_storage.save_sound_file(data, bot_id=self.bot.bot_id)
        except sound_storage.SoundValidationError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        created = await database.add_sound(
            "guild", keyword, filename, duration,
            guild_id=interaction.guild.id,
            original_filename=file.filename,
            created_by=interaction.user.id,
            bot_id=self.bot.bot_id,
        )
        if created is None:
            sound_storage.delete_sound_file(filename, bot_id=self.bot.bot_id)
            await interaction.followup.send(f"❌ 이미 등록된 키워드입니다: `{keyword}`", ephemeral=True)
            return
        log.info(
            "사운드 등록 guild_id={} keyword={} duration={:.1f}s user_id={}",
            interaction.guild.id, keyword, duration, interaction.user.id,
        )
        await interaction.followup.send(
            f"✅ 음원 등록 완료: `{keyword}` ({duration:.1f}초) — `/play {keyword}` 로 재생", ephemeral=True,
        )

    @sound_group.command(name="remove", description="이 서버에 등록된 음원을 삭제합니다")
    @app_commands.describe(keyword="삭제할 키워드")
    async def cmd_sound_remove(self, interaction: discord.Interaction, keyword: str):
        if interaction.guild is None:
            await interaction.response.send_message("❌ 서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return
        removed = await database.remove_sound(
            "guild", keyword.strip(), guild_id=interaction.guild.id, bot_id=self.bot.bot_id,
        )
        if removed is None:
            await interaction.response.send_message(
                f"❌ 이 서버에 등록된 키워드가 아닙니다: `{keyword}`", ephemeral=True,
            )
            return
        sound_storage.delete_sound_file(removed["filename"], bot_id=self.bot.bot_id)
        log.info("사운드 삭제 guild_id={} keyword={} user_id={}", interaction.guild.id, keyword, interaction.user.id)
        await interaction.response.send_message(f"🗑️ 음원 삭제 완료: `{keyword}`", ephemeral=True)

    @cmd_sound_remove.autocomplete("keyword")
    async def remove_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        sounds = await database.get_guild_sounds(interaction.guild.id, bot_id=self.bot.bot_id)
        query = current.lower()
        return [
            app_commands.Choice(name=s["keyword"], value=s["keyword"])
            for s in sounds
            if not query or query in s["keyword"].lower()
        ][:25]

    @sound_group.command(name="list", description="사용 가능한 음원 목록을 봅니다")
    async def cmd_sound_list(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ 서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return
        sounds = await database.get_sounds_for_guild(interaction.guild.id, bot_id=self.bot.bot_id)
        if not sounds:
            await interaction.response.send_message(
                "등록된 음원이 없습니다. `/sound add` 로 추가해보세요!", ephemeral=True,
            )
            return

        embed = discord.Embed(title="🔊 사운드보드", color=0x5865F2)
        guild_lines = [
            f"`{s['keyword']}` ({s['duration_seconds']:.1f}초, {s['play_count']}회)"
            for s in sounds if s["scope"] == "guild"
        ]
        global_lines = [
            f"`{s['keyword']}` ({s['duration_seconds']:.1f}초, {s['play_count']}회)"
            for s in sounds if s["scope"] == "global"
        ]
        if guild_lines:
            embed.add_field(name=f"이 서버 ({len(guild_lines)}개)", value="\n".join(guild_lines)[:1024], inline=False)
        if global_lines:
            embed.add_field(name=f"전역 ({len(global_lines)}개)", value="\n".join(global_lines)[:1024], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="play", description="등록된 음원을 재생합니다")
    @app_commands.describe(keyword="재생할 음원 키워드")
    async def cmd_play(self, interaction: discord.Interaction, keyword: str):
        if interaction.guild is None:
            await interaction.response.send_message("❌ 서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return
        keyword = keyword.strip()
        sound = await database.resolve_sound(keyword, guild_id=interaction.guild.id, bot_id=self.bot.bot_id)
        if sound is None:
            await interaction.response.send_message(f"❌ 등록되지 않은 키워드입니다: `{keyword}`", ephemeral=True)
            return

        user_voice = interaction.user.voice.channel if getattr(interaction.user, "voice", None) else None
        bot_voice_client = interaction.guild.voice_client
        target_channel = user_voice or (bot_voice_client.channel if bot_voice_client else None)
        if target_channel is None:
            await interaction.response.send_message("❌ 먼저 음성 채널에 접속해주세요!", ephemeral=True)
            return

        path = sound_storage.sound_path(sound["filename"], bot_id=sound["bot_id"])
        if not path.exists():
            await database.remove_sound_by_id(sound["id"])
            await interaction.response.send_message(
                "❌ 음원 파일이 유실되어 등록을 정리했습니다. 다시 등록해주세요.", ephemeral=True,
            )
            return

        await interaction.response.defer()
        error = await tts_engine.play_sound(str(path), target_channel, interaction.guild)
        if error:
            await interaction.followup.send(f"❌ {error}")
            return
        await database.increment_sound_play_count(sound["id"])
        log.info("사운드 재생 guild_id={} keyword={} user_id={}", interaction.guild.id, keyword, interaction.user.id)
        await interaction.followup.send(f"🔊 `{keyword}` 재생 완료")

    @cmd_play.autocomplete("keyword")
    async def play_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        return await self._keyword_autocomplete(interaction, current)


async def setup(bot: commands.Bot):
    await bot.add_cog(SoundCog(bot))
```

- [ ] **Step 2: bot.py EXTENSIONS 수정**

`bot.py:38`:

```python
EXTENSIONS = ["cogs.tts", "cogs.channels", "cogs.voice", "cogs.sounds"]
```

- [ ] **Step 3: 문법/임포트 확인 + 전체 테스트**

Run: `.\.venv\Scripts\python.exe -m py_compile cogs/sounds.py bot.py`
Expected: 출력 없음 (성공)

Run: `$env:PYTHONUTF8='1'; $env:DISCORD_TOKEN='test-token'; .\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: 30 passed

- [ ] **Step 4: Commit**

```powershell
git add cogs/sounds.py bot.py
git commit -m "feat: add /sound add|remove|list and /play slash commands"
```

---

### Task 5: 대시보드 — sounds 섹션 + 업로드/삭제 라우트

**Files:**
- Modify: `web/routes.py` (imports, `valid_sections`, index 데이터, 새 라우트)
- Modify: `web/templates/dashboard.html` (nav pill + 섹션 블록)
- Test: `tests/test_dashboard_multibot_ux.py` (string-assertion 테스트 추가)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_dashboard_multibot_ux.py` 끝에 추가:

```python
def test_dashboard_supports_sounds_section():
    routes = ROUTES.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")
    assert "sounds" in routes.split("valid_sections")[1].split("}")[0]
    assert "get_global_sounds(bot_id=selected_bot_id)" in routes
    assert "get_guild_sounds(bot_id=selected_bot_id)" in routes
    assert '@app.route("/sounds/upload", methods=["POST"])' in routes
    assert '@app.route("/sounds/<int:sound_id>/delete", methods=["POST"])' in routes
    assert "section=sounds" in template
    assert "/sounds/upload" in template
```

- [ ] **Step 2: 실패 확인**

Run: `$env:PYTHONUTF8='1'; $env:DISCORD_TOKEN='test-token'; .\.venv\Scripts\python.exe -m pytest tests/test_dashboard_multibot_ux.py -q`
Expected: 1 failed (`test_dashboard_supports_sounds_section`)

- [ ] **Step 3: routes.py 수정 — imports와 valid_sections**

`web/routes.py` 상단 imports에 추가:

```python
import sound_storage
from config import DASHBOARD_ADMIN_IDS, SOUND_MAX_KEYWORD_LENGTH, SOUND_MAX_PER_GUILD
```

(기존 `from config import DASHBOARD_ADMIN_IDS` 줄을 위 한 줄로 교체하고, `import sound_storage`는 `import database` 옆에 추가)

`register_routes` 첫 줄의 `valid_sections` 교체:

```python
    valid_sections = {"overview", "bots", "admins", "pronunciation", "audit", "sounds"}
```

- [ ] **Step 4: index()에 음원 데이터 추가**

`index()` 안에서 `guild_name_map = {g["id"]: g["name"] for g in guilds}` 줄 **다음**에 추가:

```python
        global_sounds = await database.get_global_sounds(bot_id=selected_bot_id)
        guild_sounds = await database.get_guild_sounds(bot_id=selected_bot_id)
        for s in guild_sounds:
            s["guild_name"] = guild_name_map.get(s["guild_id"], f"Unknown ({s['guild_id']})")
```

그리고 같은 함수의 `return await render_template(` 인자 목록에 추가 (`initial_guild_filter=initial_guild_filter,` 줄 다음):

```python
            global_sounds=global_sounds,
            guild_sounds=guild_sounds,
```

- [ ] **Step 5: 업로드/삭제 라우트 추가**

`web/routes.py`의 `# ───────────────────────── Admins ─────────────────────────` 주석 **앞**에 추가:

```python
    # ───────────────────────── Sounds (사운드보드) ─────────────────────────

    def redirect_sounds():
        return redirect(url_for("index", section="sounds"))

    @app.route("/sounds/upload", methods=["POST"])
    @login_required
    async def upload_sound():
        form = await request.form
        bot_id = int(form.get("bot_id") or getattr(current_app.bot, "bot_id", 1))
        scope = (form.get("scope") or "").strip()
        keyword = (form.get("keyword") or "").strip()
        raw_guild_id = (form.get("guild_id") or "").strip()

        if scope not in ("global", "guild"):
            set_notice("scope는 global 또는 guild여야 합니다.", "error")
            return redirect_sounds()
        if not keyword or len(keyword) > SOUND_MAX_KEYWORD_LENGTH:
            set_notice(f"키워드는 1~{SOUND_MAX_KEYWORD_LENGTH}자여야 합니다.", "error")
            return redirect_sounds()

        guild_id = None
        if scope == "guild":
            if not raw_guild_id.isdigit():
                set_notice("서버를 선택해야 합니다.", "error")
                return redirect_sounds()
            guild_id = int(raw_guild_id)
            if await database.get_guild_sound_count(guild_id, bot_id=bot_id) >= SOUND_MAX_PER_GUILD:
                set_notice(f"서버당 음원은 최대 {SOUND_MAX_PER_GUILD}개까지 등록할 수 있습니다.", "error")
                return redirect_sounds()

        files = await request.files
        upload = files.get("file")
        if upload is None or not upload.filename:
            set_notice("음원 파일을 선택해주세요.", "error")
            return redirect_sounds()

        data = upload.read()
        try:
            filename, duration = await sound_storage.save_sound_file(data, bot_id=bot_id)
        except sound_storage.SoundValidationError as e:
            set_notice(str(e), "error")
            return redirect_sounds()

        created = await database.add_sound(
            scope, keyword, filename, duration,
            guild_id=guild_id, original_filename=upload.filename,
            created_by=_actor_id(), bot_id=bot_id,
        )
        if created is None:
            sound_storage.delete_sound_file(filename, bot_id=bot_id)
            set_notice(f"이미 등록된 키워드: {keyword}", "error")
            return redirect_sounds()
        set_notice(f"음원 `{keyword}` 를 등록했습니다. ({duration:.1f}초)", "success")
        return redirect_sounds()

    @app.route("/sounds/<int:sound_id>/delete", methods=["POST"])
    @login_required
    async def delete_sound(sound_id: int):
        removed = await database.remove_sound_by_id(sound_id)
        if removed is None:
            set_notice("삭제할 음원을 찾을 수 없습니다.", "error")
            return redirect_sounds()
        sound_storage.delete_sound_file(removed["filename"], bot_id=removed["bot_id"])
        set_notice(f"음원 `{removed['keyword']}` 를 삭제했습니다.", "success")
        return redirect_sounds()
```

- [ ] **Step 6: 템플릿 — nav pill 추가**

`web/templates/dashboard.html`의 사이드바 nav에서 `pronunciation` nav-pill `<a>` 태그(214행 부근) **바로 다음**에, 같은 구조(아이콘 마크업 포함)를 복사해 추가하되 `section=pronunciation`을 `section=sounds`로, 라벨을 `사운드보드`로 변경:

```html
            <a href="/?section=sounds" class="nav-pill {% if active_section == 'sounds' %}active{% endif %} flex items-center gap-3 rounded-xl px-4 py-3 text-[13px] font-medium transition {% if active_section == 'sounds' %}bg-white/[0.06] text-white{% else %}text-white/40 hover:bg-white/[0.03] hover:text-white/60{% endif %}">
                사운드보드
            </a>
```

(이웃 nav-pill 안에 svg 아이콘이 있으면 동일 구조로 아이콘 마크업을 유지하고 라벨 텍스트만 교체)

- [ ] **Step 7: 템플릿 — sounds 섹션 블록 추가**

`{% elif active_section == 'audit' %}` 블록 **앞**에 추가. 카드/테이블/폼의 CSS 클래스는 이웃 섹션(bots, admins)에서 사용 중인 클래스를 그대로 따른다:

```html
        {% elif active_section == 'sounds' %}
        <section class="grid gap-6 xl:grid-cols-[1.4fr,1fr]">
            <div class="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-6">
                <h2 class="text-sm font-semibold text-white/80">등록된 음원</h2>
                <table class="mt-4 w-full text-left text-[13px]">
                    <thead class="text-white/40">
                        <tr>
                            <th class="py-2 pr-4">범위</th>
                            <th class="py-2 pr-4">키워드</th>
                            <th class="py-2 pr-4">길이</th>
                            <th class="py-2 pr-4">재생</th>
                            <th class="py-2"></th>
                        </tr>
                    </thead>
                    <tbody class="text-white/70">
                        {% for s in global_sounds %}
                        <tr class="border-t border-white/[0.04]">
                            <td class="py-2 pr-4">전역</td>
                            <td class="py-2 pr-4 font-medium text-white">{{ s.keyword }}</td>
                            <td class="py-2 pr-4">{{ '%.1f'|format(s.duration_seconds) }}초</td>
                            <td class="py-2 pr-4">{{ s.play_count }}회</td>
                            <td class="py-2 text-right">
                                <form method="post" action="/sounds/{{ s.id }}/delete" onsubmit="return confirm('음원 {{ s.keyword }} 을(를) 삭제할까요?');">
                                    <button type="submit" class="text-coral/80 hover:text-coral">삭제</button>
                                </form>
                            </td>
                        </tr>
                        {% endfor %}
                        {% for s in guild_sounds %}
                        <tr class="border-t border-white/[0.04]">
                            <td class="py-2 pr-4">{{ s.guild_name }}</td>
                            <td class="py-2 pr-4 font-medium text-white">{{ s.keyword }}</td>
                            <td class="py-2 pr-4">{{ '%.1f'|format(s.duration_seconds) }}초</td>
                            <td class="py-2 pr-4">{{ s.play_count }}회</td>
                            <td class="py-2 text-right">
                                <form method="post" action="/sounds/{{ s.id }}/delete" onsubmit="return confirm('음원 {{ s.keyword }} 을(를) 삭제할까요?');">
                                    <button type="submit" class="text-coral/80 hover:text-coral">삭제</button>
                                </form>
                            </td>
                        </tr>
                        {% endfor %}
                        {% if not global_sounds and not guild_sounds %}
                        <tr><td colspan="5" class="py-6 text-center text-white/30">등록된 음원이 없습니다.</td></tr>
                        {% endif %}
                    </tbody>
                </table>
            </div>
            <div class="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-6">
                <h2 class="text-sm font-semibold text-white/80">음원 업로드</h2>
                <p class="mt-1 text-xs text-white/40">8초 이하 · 최대 20MB · mp4/mp3/wav/ogg/webm 등</p>
                <form method="post" action="/sounds/upload" enctype="multipart/form-data" class="mt-4 grid gap-3">
                    <input type="hidden" name="bot_id" value="{{ selected_bot_id }}">
                    <select name="scope" class="rounded-lg border border-white/[0.08] bg-transparent px-3 py-2 text-sm">
                        <option value="global">전역 (모든 서버)</option>
                        <option value="guild">특정 서버</option>
                    </select>
                    <select name="guild_id" class="rounded-lg border border-white/[0.08] bg-transparent px-3 py-2 text-sm">
                        <option value="">서버 선택 (서버 범위일 때)</option>
                        {% for guild in guilds %}
                        <option value="{{ guild.id }}">{{ guild.name }}</option>
                        {% endfor %}
                    </select>
                    <input type="text" name="keyword" placeholder="키워드" maxlength="50" required class="rounded-lg border border-white/[0.08] bg-transparent px-3 py-2 text-sm">
                    <input type="file" name="file" required class="text-sm text-white/60">
                    <button type="submit" class="rounded-lg bg-white/[0.08] px-4 py-2 text-sm font-medium text-white hover:bg-white/[0.12]">업로드</button>
                </form>
            </div>
        </section>
```

- [ ] **Step 8: 테스트 통과 확인**

Run: `$env:PYTHONUTF8='1'; $env:DISCORD_TOKEN='test-token'; .\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: 31 passed

- [ ] **Step 9: Commit**

```powershell
git add web/routes.py web/templates/dashboard.html tests/test_dashboard_multibot_ux.py
git commit -m "feat: add soundboard management section to dashboard"
```

---

### Task 6: README 문서화 + 최종 검증

**Files:**
- Modify: `README.md`, `README.ko.md`

- [ ] **Step 1: README.md 갱신**

Features 목록에 추가:

```markdown
- Soundboard: register short audio clips (8 seconds max, mp4/mp3/wav/ogg/webm) with keywords and play them with `/play`
```

Slash commands 목록에 추가:

```markdown
- `/sound add`: register an audio clip (max 8s) with a keyword in the current server
- `/sound remove`: delete a sound registered in the current server
- `/sound list`: list available sounds (server + global)
- `/play`: play a registered sound in your voice channel
```

"Not included" 파일 목록에 `data/sounds/` 추가:

```text
data/sounds/
```

- [ ] **Step 2: README.ko.md 갱신**

같은 위치(기능 목록, 슬래시 명령어 목록, 미포함 파일 목록)에 한국어로 동일 내용 추가:

```markdown
- 사운드보드: 8초 이하 음원(mp4/mp3/wav/ogg/webm)을 키워드로 등록하고 `/play`로 재생
```

```markdown
- `/sound add`: 현재 서버에 음원(8초 이하)을 키워드와 함께 등록
- `/sound remove`: 현재 서버에 등록된 음원 삭제
- `/sound list`: 사용 가능한 음원 목록 (서버 + 전역)
- `/play`: 등록된 음원을 음성 채널에서 재생
```

- [ ] **Step 3: 최종 전체 테스트**

Run: `$env:PYTHONUTF8='1'; $env:DISCORD_TOKEN='test-token'; .\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: 31 passed

- [ ] **Step 4: Commit**

```powershell
git add README.md README.ko.md
git commit -m "docs: document soundboard feature and commands"
```

---

## 수동 검증 (구현 완료 후, 실제 Discord에서)

자동화 불가 영역 — Docker 또는 로컬에서 봇을 실제 실행해 확인:

1. `/sound add 키워드:테스트 file:(8초 이하 mp4)` → "✅ 음원 등록 완료" + `data/sounds/1/*.ogg` 생성 확인
2. 9초짜리 파일 업로드 → "❌ 음원 길이는 8초 이하" 거부 확인
3. 음성 채널 접속 후 `/play 테스트` → 음원 재생 확인 (TTS 재생 중이면 순서 대기)
4. `/sound list`, `/sound remove` 동작 확인
5. 대시보드 → 사운드보드 섹션 → 전역 음원 업로드/삭제 확인
6. 워커 봇(bot_id≠1)에서도 동일 동작 확인 (`data/sounds/<bot_id>/` 분리)
