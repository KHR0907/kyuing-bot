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
    monkeypatch.setattr(sound_storage, "SOUND_MAX_DURATION_SECONDS", 8.0)

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
