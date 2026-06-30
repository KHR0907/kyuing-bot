import os
import asyncio
from collections import defaultdict

import discord
from loguru import logger as log

import database
import music_handoff
from tts_engines import get_engine

# 서버별 TTS 큐 락
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

GOOGLE_TTS_MAX_INPUT_BYTES = 5000
SUPERTONIC_MAX_INPUT_CHARS = 1000


def validate_tts_input_limit(text: str, engine_name: str) -> str | None:
    """Return a user-facing error if text exceeds the selected engine input limit."""
    if engine_name == "google":
        if len(text.encode("utf-8")) > GOOGLE_TTS_MAX_INPUT_BYTES:
            return "텍스트가 너무 깁니다. (Google TTS 최대 5,000 bytes)"
        return None

    if len(text) > SUPERTONIC_MAX_INPUT_CHARS:
        return "텍스트가 너무 깁니다. (Supertonic 최대 1000자)"
    return None


def apply_keyword_replacement(text: str, guild_id: int, *, bot_id: int | None = None) -> str:
    """Apply bot/guild scoped pronunciation replacement before any TTS engine runs."""
    replaced_text, replacement_scope = database.resolve_keyword_replacement(guild_id, text, bot_id=bot_id)
    if not replacement_scope:
        return text

    log.info(
        "TTS 키워드 치환 scope={} guild_id={} keyword={} replacement={}",
        replacement_scope,
        guild_id,
        text,
        replaced_text,
    )
    database.record_keyword_hit(
        replacement_scope,
        text,
        guild_id if replacement_scope == "guild" else None,
        bot_id=bot_id,
    )
    return replaced_text


async def _ensure_voice_client(guild: discord.Guild, voice_channel: discord.VoiceChannel):
    """음성 클라이언트를 voice_channel에 연결된 상태로 만든다 (stale이면 강제 재연결)."""
    vc = guild.voice_client
    if vc is None or not vc.is_connected():
        if vc is not None:
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
        vc = await voice_channel.connect()
    elif vc.channel != voice_channel:
        await vc.move_to(voice_channel)
    return vc


async def _play_with_handoff(guild, voice_channel, base_play):
    """음악 재생 중이면 핸드오프(suspend→base_play→resume), 아니면 base_play만.

    base_play: 실제 FFmpeg 재생을 수행하는 async 콜러블 (인자 없음).
    """
    player = music_handoff.get_active_music_player(guild)
    if player is None:
        await base_play()
        return

    resume_track, resume_ms = await music_handoff.suspend_music(player)
    try:
        await base_play()
    finally:
        await music_handoff.resume_music(voice_channel, resume_track, resume_ms)
        # resume_track/resume_ms 는 여기서 마지막 사용, 함수 종료로 휘발


async def do_tts(
    text: str,
    voice_channel: discord.VoiceChannel,
    guild: discord.Guild,
    user_id: int,
    voice: str | None = None,
    speed: float | None = None,
    lang: str | None = None,
    total_steps: int | None = None,
    bot_id: int | None = None,
) -> str | None:
    settings = await database.get_user_settings(user_id, bot_id=bot_id)
    engine_name = settings["engine"]
    voice = voice or settings["voice"]
    speed = speed if speed is not None else settings["speed"]
    lang = lang or settings["lang"]
    total_steps = total_steps if total_steps is not None else settings["total_steps"]

    text = apply_keyword_replacement(text, guild.id, bot_id=bot_id)
    limit_error = validate_tts_input_limit(text, engine_name)
    if limit_error:
        return limit_error

    engine = get_engine(engine_name)

    async with _locks[guild.id]:
        tmp_path = None
        try:
            tmp_path = await engine.synthesize(
                text, voice=voice, speed=speed, lang=lang,
                total_steps=total_steps, bot_id=bot_id,
            )

            async def _base_play():
                vc = await _ensure_voice_client(guild, voice_channel)
                if vc.is_playing():
                    vc.stop()
                vc.play(discord.FFmpegPCMAudio(tmp_path))
                while vc.is_playing():
                    await asyncio.sleep(0.5)

            await _play_with_handoff(guild, voice_channel, _base_play)
            return None

        except Exception as e:
            return f"TTS 오류: {str(e)}"
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


async def play_sound(
    file_path: str,
    voice_channel: discord.VoiceChannel,
    guild: discord.Guild,
) -> str | None:
    """사운드보드 음원 파일을 재생한다. TTS와 같은 길드 락을 공유해 순서를 보장한다."""
    async with _locks[guild.id]:
        try:
            async def _base_play():
                vc = await _ensure_voice_client(guild, voice_channel)
                if vc.is_playing():
                    vc.stop()
                vc.play(discord.FFmpegPCMAudio(file_path))
                while vc.is_playing():
                    await asyncio.sleep(0.5)
                if vc.is_playing():
                    vc.stop()

            await _play_with_handoff(guild, voice_channel, _base_play)
            return None
        except Exception as e:
            return f"사운드 재생 오류: {str(e)}"
