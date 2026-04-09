import asyncio
import tempfile

from loguru import logger as log

from tts_engines.base import TTSEngineBase

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from supertonic import TTS

        log.info("Supertonic-2 모델 로딩 중...")
        _engine = TTS(auto_download=True)
        log.info("모델 로딩 완료")
    return _engine


class SupertonicEngine(TTSEngineBase):
    name = "supertonic"

    def get_voices(self) -> dict[str, str]:
        from config import SUPERTONIC_VOICES

        return dict(SUPERTONIC_VOICES)

    async def synthesize(self, text: str, voice: str, speed: float, lang: str, **kwargs) -> str:
        total_steps = kwargs.get("total_steps", 2)
        engine = _get_engine()
        voice_style = engine.get_voice_style(voice_name=voice)

        loop = asyncio.get_event_loop()
        wav, _duration = await loop.run_in_executor(
            None,
            lambda: engine.synthesize(
                text,
                voice_style=voice_style,
                lang=lang,
                speed=speed,
                total_steps=total_steps,
            ),
        )

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        engine.save_audio(wav, tmp.name)
        tmp_path = tmp.name
        tmp.close()
        return tmp_path
