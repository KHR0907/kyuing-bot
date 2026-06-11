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
