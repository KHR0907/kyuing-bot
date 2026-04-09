import os
import asyncio
from collections import defaultdict

import discord
from loguru import logger as log

import database
from tts_engines import get_engine

# 서버별 TTS 큐 락
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


async def do_tts(
    text: str,
    voice_channel: discord.VoiceChannel,
    guild: discord.Guild,
    user_id: int,
    voice: str | None = None,
    speed: float | None = None,
    lang: str | None = None,
    total_steps: int | None = None,
) -> str | None:
    settings = await database.get_user_settings(user_id)
    engine_name = settings["engine"]
    voice = voice or settings["voice"]
    speed = speed if speed is not None else settings["speed"]
    lang = lang or settings["lang"]
    total_steps = total_steps if total_steps is not None else settings["total_steps"]

    if len(text) > 1000:
        return "텍스트가 너무 깁니다. (최대 1000자)"

    engine = get_engine(engine_name)

    async with _locks[guild.id]:
        tmp_path = None
        try:
            tmp_path = await engine.synthesize(
                text, voice=voice, speed=speed, lang=lang,
                total_steps=total_steps,
            )

            vc = guild.voice_client
            if vc is None:
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
