import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from loguru import logger as log

from config import SUPERTONIC_DEFAULT_STEPS
from tts_engines.base import TTSEngineBase

_engine = None


def _active_model_name() -> str:
    """패키지가 실제로 다운로드/사용할 모델 이름."""
    from supertonic.config import DEFAULT_MODEL

    return os.environ.get("SUPERTONIC_MODEL") or DEFAULT_MODEL


def prune_unused_model_caches() -> None:
    """사용 중인 supertonic 모델 외의 캐시를 정리한다.

    - `~/.cache/supertonic*` 중 현재 모델의 cache_dir 외의 디렉토리 삭제
    - `~/.cache/huggingface/hub/models--Supertone--*` 중 현재 모델 repo 외 삭제
    - `SUPERTONIC_CACHE_DIR` 환경변수가 설정된 경우(모든 모델이 한 디렉토리를 공유)
      충돌 위험이 있어 정리를 건너뛴다
    """
    if os.environ.get("SUPERTONIC_CACHE_DIR"):
        log.debug("SUPERTONIC_CACHE_DIR 설정됨 — 캐시 정리 건너뜀")
        return

    try:
        from supertonic.config import MODEL_CONFIGS
    except Exception as exc:  # pragma: no cover
        log.warning("supertonic 패키지에서 MODEL_CONFIGS를 읽을 수 없음: {}", exc)
        return

    active = _active_model_name()
    if active not in MODEL_CONFIGS:
        log.warning("알 수 없는 supertonic 모델 '{}' — 캐시 정리 건너뜀", active)
        return

    active_cfg = MODEL_CONFIGS[active]
    keep_cache_dir = active_cfg["cache_dir"]
    keep_repo = active_cfg["repo"]

    home_cache = Path.home() / ".cache"
    freed = 0

    # 1) ~/.cache/supertonic* 정리
    for name, cfg in MODEL_CONFIGS.items():
        if name == active:
            continue
        path = home_cache / cfg["cache_dir"]
        if path == home_cache / keep_cache_dir:
            continue
        if path.exists():
            size = _dir_size(path)
            try:
                shutil.rmtree(path)
                freed += size
                log.info("이전 supertonic 모델 캐시 삭제: {} ({:.1f} MB)", path, size / 1_048_576)
            except OSError as exc:
                log.warning("캐시 삭제 실패 {}: {}", path, exc)

    # 2) HuggingFace hub 캐시 정리
    hf_hub = home_cache / "huggingface" / "hub"
    if hf_hub.is_dir():
        keep_hub_name = "models--" + keep_repo.replace("/", "--")
        for name, cfg in MODEL_CONFIGS.items():
            if name == active:
                continue
            hub_name = "models--" + cfg["repo"].replace("/", "--")
            if hub_name == keep_hub_name:
                continue
            path = hf_hub / hub_name
            if path.exists():
                size = _dir_size(path)
                try:
                    shutil.rmtree(path)
                    freed += size
                    log.info("이전 HF hub 캐시 삭제: {} ({:.1f} MB)", path, size / 1_048_576)
                except OSError as exc:
                    log.warning("HF hub 캐시 삭제 실패 {}: {}", path, exc)

    if freed:
        log.info("supertonic 캐시 정리 완료 — 총 {:.1f} MB 회수", freed / 1_048_576)


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _get_engine():
    global _engine
    if _engine is None:
        from supertonic import TTS

        log.info("Supertonic-3 모델 로딩 중...")
        _engine = TTS(auto_download=True)
        log.info("모델 로딩 완료")
    return _engine


class SupertonicEngine(TTSEngineBase):
    name = "supertonic"

    def get_voices(self) -> dict[str, str]:
        from config import SUPERTONIC_VOICES

        return dict(SUPERTONIC_VOICES)

    async def synthesize(self, text: str, voice: str, speed: float, lang: str, **kwargs) -> str:
        total_steps = kwargs.get("total_steps", SUPERTONIC_DEFAULT_STEPS)
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
