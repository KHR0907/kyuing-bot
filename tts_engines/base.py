from abc import ABC, abstractmethod


class TTSEngineBase(ABC):
    """TTS 엔진 추상 기반 클래스."""

    name: str

    @abstractmethod
    async def synthesize(self, text: str, voice: str, speed: float, lang: str, **kwargs) -> str:
        """텍스트를 음성으로 합성하여 임시 오디오 파일 경로를 반환합니다."""
        ...

    @abstractmethod
    def get_voices(self) -> dict[str, str]:
        """사용 가능한 보이스 목록을 반환합니다. {id: 표시명}"""
        ...
