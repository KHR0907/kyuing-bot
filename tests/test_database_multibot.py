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
    assert "token" not in bots[0]
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
async def test_stopped_desired_state_is_not_auto_started(db):
    assert [bot["id"] for bot in await db.get_enabled_bots()] == [1]
    await db.set_bot_desired_state(1, "stopped")
    assert await db.get_enabled_bots() == []
    await db.set_bot_desired_state(1, "running")
    assert [bot["id"] for bot in await db.get_enabled_bots()] == [1]


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
async def test_config_revision_changes_and_refreshes_process_cache(db):
    initial_revision = await db.get_config_revision(bot_id=1)
    assert await db.add_global_keyword_alias("캐시", "첫 값", bot_id=1)
    changed_revision = await db.get_config_revision(bot_id=1)
    assert changed_revision == initial_revision + 1

    # Simulate a different process committing a dashboard mutation. The local
    # process cache stays old until its revision poll runs.
    await db._db.execute(
        "UPDATE global_keyword_aliases_v2 SET replacement = ? WHERE bot_id = ? AND keyword = ?",
        ("둘째 값", 1, "캐시"),
    )
    await db._bump_config_revision(1)
    await db._db.commit()
    assert db.resolve_keyword_replacement(999, "캐시", bot_id=1) == ("첫 값", "global")

    latest_revision = await db.refresh_cache_if_changed(changed_revision, bot_id=1)
    assert latest_revision == changed_revision + 1
    assert db.resolve_keyword_replacement(999, "캐시", bot_id=1) == ("둘째 값", "global")


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


@pytest.mark.asyncio
async def test_guild_snapshots_are_scoped_and_remove_stale_guilds(db):
    await db.sync_bot_guild_snapshots(1, [
        {"id": 10, "name": "One", "member_count": 3, "voice_channel_id": 100,
         "voice_channel_name": "General"},
        {"id": 11, "name": "Old", "member_count": 1},
    ])
    await db.sync_bot_guild_snapshots(2, [
        {"id": 20, "name": "Two", "member_count": 30},
    ])

    await db.sync_bot_guild_snapshots(1, [
        {"id": 10, "name": "One renamed", "member_count": 4},
    ])
    one = await db.get_bot_guild_snapshots(1)
    two = await db.get_bot_guild_snapshots(2)
    assert [guild["id"] for guild in one] == [10]
    assert one[0]["name"] == "One renamed"
    assert [guild["id"] for guild in two] == [20]
