import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _DummyLogger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


sys.modules.setdefault("loguru", types.SimpleNamespace(logger=_DummyLogger()))
sys.modules.setdefault(
    "discord",
    types.SimpleNamespace(
        VoiceChannel=object,
        Guild=object,
        FFmpegPCMAudio=lambda path: path,
    ),
)


def reload_tts_engine():
    import tts_engine

    return importlib.reload(tts_engine)


@pytest.mark.asyncio
async def test_do_tts_applies_keyword_replacement_before_engine_synthesis(monkeypatch, tmp_path):
    tts_engine = reload_tts_engine()

    calls = []

    async def fake_get_user_settings(user_id, bot_id=None):
        return {
            "engine": "google",
            "voice": "ko-KR-Standard-A",
            "speed": 1.0,
            "lang": "ko",
            "total_steps": 8,
        }

    class FakeDatabase:
        async def get_user_settings(self, user_id, bot_id=None):
            calls.append(("settings", user_id, bot_id))
            return await fake_get_user_settings(user_id, bot_id=bot_id)

        def resolve_keyword_replacement(self, guild_id, text, bot_id=None):
            calls.append(("resolve", guild_id, text, bot_id))
            return ("치환된 문장", "global")

        def record_keyword_hit(self, scope, keyword, guild_id=None, bot_id=None):
            calls.append(("hit", scope, keyword, guild_id, bot_id))

    class FakeEngine:
        async def synthesize(self, text, **kwargs):
            calls.append(("synthesize", text, kwargs))
            path = tmp_path / "out.wav"
            path.write_bytes(b"wav")
            return str(path)

    class FakeVoiceClient:
        channel = object()

        def is_connected(self):
            return True

        def is_playing(self):
            return False

        def play(self, audio):
            calls.append(("play", audio))

    guild = types.SimpleNamespace(id=777, voice_client=FakeVoiceClient())
    voice_channel = guild.voice_client.channel

    monkeypatch.setattr(tts_engine, "database", FakeDatabase())
    monkeypatch.setattr(tts_engine, "get_engine", lambda engine_name: FakeEngine())
    monkeypatch.setattr(tts_engine.asyncio, "sleep", lambda *_: (_ for _ in ()).throw(AssertionError("should not sleep")))

    error = await tts_engine.do_tts(
        text="원본 키워드",
        voice_channel=voice_channel,
        guild=guild,
        user_id=123,
        bot_id=2,
    )

    assert error is None
    assert ("settings", 123, 2) in calls
    assert ("resolve", 777, "원본 키워드", 2) in calls
    assert ("hit", "global", "원본 키워드", None, 2) in calls
    synthesize_calls = [call for call in calls if call[0] == "synthesize"]
    assert synthesize_calls[0][1] == "치환된 문장"


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


@pytest.mark.parametrize(
    ("engine", "text", "expected_error"),
    [
        ("google", "a" * 5000, None),
        ("google", "a" * 5001, "텍스트가 너무 깁니다. (Google TTS 최대 5,000 bytes)"),
        ("google", "가" * 1666, None),
        ("google", "가" * 1667, "텍스트가 너무 깁니다. (Google TTS 최대 5,000 bytes)"),
        ("supertonic", "a" * 1001, "텍스트가 너무 깁니다. (Supertonic 최대 1000자)"),
    ],
)
def test_validate_tts_input_limit_uses_engine_specific_google_byte_limit(engine, text, expected_error):
    tts_engine = reload_tts_engine()

    assert tts_engine.validate_tts_input_limit(text, engine) == expected_error


@pytest.mark.asyncio
async def test_play_sound_waits_until_playback_finishes(monkeypatch, tmp_path):
    tts_engine = reload_tts_engine()

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(tts_engine.asyncio, "sleep", fake_sleep)

    class FakeVoiceClient:
        channel = object()

        def __init__(self):
            self._polls_remaining = 4

        def is_connected(self):
            return True

        def is_playing(self):
            if self._polls_remaining > 0:
                self._polls_remaining -= 1
                return True
            return False

        def stop(self):
            pass

        def play(self, audio):
            pass

    guild = types.SimpleNamespace(id=999, voice_client=FakeVoiceClient())
    sound_file = tmp_path / "sound.ogg"
    sound_file.write_bytes(b"ogg")

    error = await tts_engine.play_sound(str(sound_file), guild.voice_client.channel, guild)

    assert error is None
    assert sleep_calls == [0.5, 0.5, 0.5]
