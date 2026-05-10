import importlib
import os
import sys
import types

import pytest
import pytest_asyncio


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
async def test_init_db_seeds_default_bot(db):
    bots = await db.get_bots()
    assert len(bots) == 1
    assert bots[0]["id"] == 1
    assert bots[0]["name"] == "Default Bot"
    assert await db.get_bot_token(1) == "default-token"


@pytest.mark.asyncio
async def test_create_bot_rejects_duplicate_discord_user_id(db):
    first = await db.create_bot(
        "second", "token-2", discord_bot_user_id=222, discord_username="bot-two"
    )
    assert first["id"] == 2

    duplicate = await db.create_bot(
        "dup", "token-dup", discord_bot_user_id=222, discord_username="bot-two-dup"
    )
    assert duplicate is None


@pytest.mark.asyncio
async def test_tts_channels_are_scoped_by_bot_id(db):
    assert await db.add_tts_channel(10, 100, bot_id=1) is True
    assert await db.add_tts_channel(10, 100, bot_id=2) is True

    assert db.get_tts_channels_cached(10, bot_id=1) == [100]
    assert db.get_tts_channels_cached(10, bot_id=2) == [100]

    assert await db.remove_tts_channel(10, 100, bot_id=1) is True
    assert db.get_tts_channels_cached(10, bot_id=1) == []
    assert db.get_tts_channels_cached(10, bot_id=2) == [100]


@pytest.mark.asyncio
async def test_keyword_aliases_are_scoped_by_bot_id(db):
    assert await db.add_global_keyword_alias("ㅎㅇ", "안녕", bot_id=1)
    assert await db.add_global_keyword_alias("ㅎㅇ", "하이", bot_id=2)
    assert await db.add_guild_keyword_alias(123, "ㅎㅇ", "서버안녕", bot_id=2)

    assert db.resolve_keyword_replacement(123, "ㅎㅇ", bot_id=1) == ("안녕", "global")
    assert db.resolve_keyword_replacement(999, "ㅎㅇ", bot_id=2) == ("하이", "global")
    assert db.resolve_keyword_replacement(123, "ㅎㅇ", bot_id=2) == ("서버안녕", "guild")


@pytest.mark.asyncio
async def test_daily_stats_are_scoped_by_bot_id(db):
    await db.record_daily_snapshot(3, 4, bot_id=1)
    await db.record_daily_snapshot(30, 40, bot_id=2)
    await db.increment_daily_tts_requests(bot_id=1)
    await db.increment_daily_tts_requests(5, bot_id=2)

    one = await db.get_dashboard_metrics(3, 4, bot_id=1)
    two = await db.get_dashboard_metrics(30, 40, bot_id=2)

    assert one["daily_requests"] == 1
    assert two["daily_requests"] == 5
    project = await db.get_project_metrics()
    assert project["bot_count"] == 1  # only enabled seeded bot unless explicitly enabled
    assert project["daily_requests"] == 6
