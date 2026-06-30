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

import tts_engine
import music_handoff

pytestmark = pytest.mark.asyncio


async def test_no_music_skips_handoff(monkeypatch):
    """음악 미재생이면 suspend/resume이 호출되지 않는다."""
    monkeypatch.setattr(music_handoff, "get_active_music_player", lambda g: None)

    suspend_called = {"n": 0}
    monkeypatch.setattr(music_handoff, "suspend_music",
                        lambda p: suspend_called.__setitem__("n", suspend_called["n"] + 1))

    # _play_with_handoff은 player None이면 곧장 base_play를 호출해야 한다
    played = {"n": 0}
    async def base_play():
        played["n"] += 1

    await tts_engine._play_with_handoff(guild=object(), voice_channel=object(), base_play=base_play)
    assert played["n"] == 1
    assert suspend_called["n"] == 0


async def test_music_active_triggers_suspend_and_resume(monkeypatch):
    """음악 재생 중이면 suspend → base_play → resume 순서로 실행."""
    order = []

    fake_player = object()
    monkeypatch.setattr(music_handoff, "get_active_music_player", lambda g: fake_player)

    async def fake_suspend(p):
        order.append("suspend")
        return ("TRACK", 4800)
    async def fake_resume(ch, track, ms):
        order.append(("resume", track, ms))
    monkeypatch.setattr(music_handoff, "suspend_music", fake_suspend)
    monkeypatch.setattr(music_handoff, "resume_music", fake_resume)

    async def base_play():
        order.append("play")

    ch = object()
    await tts_engine._play_with_handoff(guild=object(), voice_channel=ch, base_play=base_play)
    assert order == ["suspend", "play", ("resume", "TRACK", 4800)]


async def test_resume_runs_even_if_base_play_raises(monkeypatch):
    """base_play 예외에도 음악 재개를 시도한다."""
    order = []
    monkeypatch.setattr(music_handoff, "get_active_music_player", lambda g: object())
    async def fake_suspend(p):
        return ("TRACK", 100)
    async def fake_resume(ch, track, ms):
        order.append("resume")
    monkeypatch.setattr(music_handoff, "suspend_music", fake_suspend)
    monkeypatch.setattr(music_handoff, "resume_music", fake_resume)

    async def base_play():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await tts_engine._play_with_handoff(guild=object(), voice_channel=object(), base_play=base_play)
    assert order == ["resume"]
