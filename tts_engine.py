import os
import asyncio
import tempfile
from collections import defaultdict

import discord
from loguru import logger as log
from supertonic import TTS

import database

log.info("Supertonic-2 모델 로딩 중...")
_engine = TTS(auto_download=True)
log.info("모델 로딩 완료")

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
    voice = voice or settings["voice"]
    speed = speed if speed is not None else settings["speed"]
    lang = lang or settings["lang"]
    total_steps = total_steps if total_steps is not None else settings["total_steps"]

    if len(text) > 1000:
        return "텍스트가 너무 깁니다. (최대 1000자)"

    async with _locks[guild.id]:
        tmp_path = None
        try:
            voice_style = _engine.get_voice_style(voice_name=voice)
            loop = asyncio.get_event_loop()
            wav, duration = await loop.run_in_executor(
                None,
                lambda: _engine.synthesize(
                    text,
                    voice_style=voice_style,
                    lang=lang,
                    speed=speed,
                    total_steps=total_steps,
                ),
            )

            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            _engine.save_audio(wav, tmp.name)
            tmp_path = tmp.name
            tmp.close()

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
