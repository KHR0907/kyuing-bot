import os
import asyncio
from collections import defaultdict

import discord
from loguru import logger as log

import database
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

            if vc.is_playing():
                vc.stop()

            vc.play(discord.FFmpegPCMAudio(tmp_path))

            while vc.is_playing():
                await asyncio.sleep(0.5)

            return None

        except Exception as e:
            return f"TTS 오류: {str(e)}"
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
