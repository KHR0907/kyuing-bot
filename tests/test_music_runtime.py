import importlib

import pytest

pytestmark = pytest.mark.asyncio


def test_is_music_available_false_when_disabled(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("MUSIC_ENABLED", "false")
    import config
    importlib.reload(config)
    import music_runtime
    importlib.reload(music_runtime)
    music_runtime.mark_pool_connected(True)
    assert music_runtime.is_music_available() is False


def test_is_music_available_requires_pool(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("MUSIC_ENABLED", "true")
    import config
    importlib.reload(config)
    import music_runtime
    importlib.reload(music_runtime)
    music_runtime.mark_pool_connected(False)
    assert music_runtime.is_music_available() is False
    if music_runtime.WAVELINK_AVAILABLE:
        music_runtime.mark_pool_connected(True)
        assert music_runtime.is_music_available() is True


async def test_connect_pool_returns_false_when_disabled(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("MUSIC_ENABLED", "false")
    import config
    importlib.reload(config)
    import music_runtime
    importlib.reload(music_runtime)
    result = await music_runtime.connect_pool(object())
    assert result is False
