import importlib
import os


def _reload_config():
    import config
    return importlib.reload(config)


def test_lavalink_defaults(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    for key in ("LAVALINK_HOST", "LAVALINK_PORT", "LAVALINK_PASSWORD", "MUSIC_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    config = _reload_config()
    assert config.LAVALINK_HOST == "lavalink"
    assert config.LAVALINK_PORT == 2333
    assert config.LAVALINK_PASSWORD == "youshallnotpass"
    assert config.LAVALINK_URI == "http://lavalink:2333"
    assert config.MUSIC_ENABLED is True


def test_music_disabled_flag(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("MUSIC_ENABLED", "false")
    config = _reload_config()
    assert config.MUSIC_ENABLED is False


def test_lavalink_uri_uses_env(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("LAVALINK_HOST", "ll.example.com")
    monkeypatch.setenv("LAVALINK_PORT", "9999")
    config = _reload_config()
    assert config.LAVALINK_URI == "http://ll.example.com:9999"
