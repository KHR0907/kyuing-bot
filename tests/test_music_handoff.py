import pytest

import music_handoff

pytestmark = pytest.mark.asyncio


def test_compute_resume_ms_airbag():
    assert music_handoff.compute_resume_ms(5000) == 4800
    assert music_handoff.AIRBAG_MS == 200


def test_compute_resume_ms_clamps_to_zero():
    assert music_handoff.compute_resume_ms(100) == 0
    assert music_handoff.compute_resume_ms(0) == 0


def test_get_active_music_player_none_when_unavailable(monkeypatch):
    monkeypatch.setattr(music_handoff.music_runtime, "is_music_available", lambda: False)
    guild = type("G", (), {"voice_client": object()})()
    assert music_handoff.get_active_music_player(guild) is None


async def test_suspend_music_returns_position_and_pauses(monkeypatch):
    calls = {}

    class FakeTrack: ...
    track = FakeTrack()

    class FakePlayer:
        current = track
        position = 5000
        async def pause(self, value): calls["paused"] = value
        async def disconnect(self): calls["disconnected"] = True

    resume_track, resume_ms = await music_handoff.suspend_music(FakePlayer())
    assert resume_track is track
    assert resume_ms == 4800
    assert calls["paused"] is True
    assert calls["disconnected"] is True


async def test_suspend_music_handles_none():
    assert await music_handoff.suspend_music(None) == (None, None)


async def test_resume_music_noop_without_track():
    # 트랙이 없으면 아무 것도 하지 않고 조용히 반환
    await music_handoff.resume_music(object(), None, 0)


async def test_resume_music_reconnects_and_plays(monkeypatch):
    calls = {}

    class FakePlayer:
        async def play(self, track, start=0): calls["played"] = (track, start)

    fake_player = FakePlayer()

    class FakeChannel:
        async def connect(self, cls=None):
            calls["connected_cls"] = cls
            return fake_player

    track = object()
    monkeypatch.setattr(music_handoff, "_player_cls", lambda: FakePlayer)
    await music_handoff.resume_music(FakeChannel(), track, 4800)
    assert calls["played"] == (track, 4800)
