from tts_engines.base import TTSEngineBase
from tts_engines.supertonic_engine import SupertonicEngine
from tts_engines.google_engine import GoogleEngine

_engines: dict[str, TTSEngineBase] = {
    "supertonic": SupertonicEngine(),
    "google": GoogleEngine(),
}


def get_engine(name: str) -> TTSEngineBase:
    engine = _engines.get(name)
    if engine is None:
        raise ValueError(f"알 수 없는 TTS 엔진: {name}")
    return engine


def get_engine_names() -> list[str]:
    return list(_engines.keys())
