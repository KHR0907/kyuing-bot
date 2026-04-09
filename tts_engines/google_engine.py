import asyncio
import tempfile

from loguru import logger as log

from tts_engines.base import TTSEngineBase

_client = None


def _get_client():
    global _client
    if _client is None:
        from google.cloud import texttospeech
        from config import GOOGLE_TTS_API_KEY

        _client = texttospeech.TextToSpeechClient(
            client_options={"api_key": GOOGLE_TTS_API_KEY},
        )
        log.info("Google TTS 클라이언트 초기화 완료")
    return _client


class GoogleEngine(TTSEngineBase):
    name = "google"

    def get_voices(self) -> dict[str, str]:
        from config import GOOGLE_VOICES

        return dict(GOOGLE_VOICES)

    async def synthesize(self, text: str, voice: str, speed: float, lang: str, **kwargs) -> str:
        from google.cloud import texttospeech

        import database

        client = _get_client()
        lang_code = voice.rsplit("-", 1)[0].rsplit("-", 1)[0]  # "ko-KR-Standard-A" → "ko-KR"

        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=lang_code,
            name=voice,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            speaking_rate=speed,
        )

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.synthesize_speech(
                input=synthesis_input,
                voice=voice_params,
                audio_config=audio_config,
            ),
        )

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.write(response.audio_content)
        tmp_path = tmp.name
        tmp.close()

        char_count = len(text)
        await database.increment_tts_char_usage("standard", char_count)
        log.debug("Google TTS 사용: voice={} chars={}", voice, char_count)

        return tmp_path
