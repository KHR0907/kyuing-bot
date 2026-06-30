"""음악 재생 중 TTS/사운드 인터럽트를 위한 연결 핸드오프 (휘발성 상태)."""
from loguru import logger as log

import music_runtime

AIRBAG_MS = 200


def _player_cls():
    """테스트에서 monkeypatch 가능하도록 wavelink.Player를 함수로 노출."""
    import wavelink
    return wavelink.Player


def compute_resume_ms(position_ms: int) -> int:
    return max(0, position_ms - AIRBAG_MS)


def get_active_music_player(guild):
    """이 길드에 재생 중인 음악 Player를 반환, 없으면 None."""
    if not music_runtime.WAVELINK_AVAILABLE or not music_runtime.is_music_available():
        return None
    import wavelink
    player = getattr(guild, "voice_client", None)
    if isinstance(player, wavelink.Player) and player.playing:
        return player
    return None


async def suspend_music(player):
    """음악을 멈추고 연결을 해제. (resume_track, resume_ms) 휘발성 상태 반환."""
    if player is None:
        return (None, None)
    resume_track = player.current
    resume_ms = compute_resume_ms(player.position)
    await player.pause(True)
    await player.disconnect()
    return (resume_track, resume_ms)


async def resume_music(voice_channel, resume_track, resume_ms) -> None:
    """음악 Player로 재연결해 resume_ms 위치부터 재생. 실패는 삼킨다."""
    if resume_track is None:
        return
    # 대상 채널에 사람이 없으면(빈 채널) 재연결 생략
    members = getattr(voice_channel, "members", None)
    if members is not None and all(getattr(m, "bot", False) for m in members):
        return
    try:
        player = await voice_channel.connect(cls=_player_cls())
        await player.play(resume_track, start=resume_ms)
    except Exception as e:
        log.warning("음악 재개 실패 — 포기: {}", e)
