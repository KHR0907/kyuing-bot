"""사운드보드 음원 파일 저장/검증 파이프라인.

슬래시 명령어와 대시보드가 공유하는 업로드 공통 로직:
크기 검증 → ffprobe(오디오 스트림 + 길이) → ffmpeg(오디오만 추출, opus 변환)
→ data/sounds/<bot_id>/<uuid>.ogg 저장
"""

import asyncio
import json
import os
import tempfile
import uuid
from contextlib import suppress
from pathlib import Path

from loguru import logger as log

from config import SOUND_MAX_DURATION_SECONDS, SOUND_MAX_FILE_BYTES, SOUNDS_DIR


class SoundValidationError(Exception):
    """사용자에게 그대로 안내 가능한 검증 실패 메시지."""


def sound_path(filename: str, *, bot_id: int) -> Path:
    return Path(SOUNDS_DIR) / str(int(bot_id)) / filename


async def _run(*args: str) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout, stderr


def parse_probe_output(raw: str) -> float | None:
    """ffprobe JSON 출력에서 오디오 길이(초)를 꺼낸다. 오디오 스트림이 없으면 None."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    streams = data.get("streams") or []
    if not any(s.get("codec_type") == "audio" for s in streams):
        return None
    try:
        return float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        return None


async def probe_audio_duration(path: str) -> float | None:
    rc, stdout, _stderr = await _run(
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type",
        "-show_entries", "format=duration",
        "-of", "json", path,
    )
    if rc != 0:
        return None
    return parse_probe_output(stdout.decode("utf-8", errors="replace"))


async def save_sound_file(data: bytes, *, bot_id: int) -> tuple[str, float]:
    """업로드 데이터를 검증·변환해 저장하고 (파일명, 길이초)를 돌려준다.

    검증 실패 시 SoundValidationError.
    """
    if len(data) > SOUND_MAX_FILE_BYTES:
        mb = SOUND_MAX_FILE_BYTES // (1024 * 1024)
        raise SoundValidationError(f"파일이 너무 큽니다. (최대 {mb}MB)")

    src = tempfile.NamedTemporaryFile(delete=False)
    try:
        src.write(data)
        src.close()

        duration = await probe_audio_duration(src.name)
        if duration is None:
            raise SoundValidationError("오디오 트랙을 찾을 수 없는 파일입니다.")
        if duration > SOUND_MAX_DURATION_SECONDS:
            raise SoundValidationError(
                f"음원 길이는 {SOUND_MAX_DURATION_SECONDS:.0f}초 이하여야 합니다. (현재 {duration:.1f}초)"
            )

        dest_dir = Path(SOUNDS_DIR) / str(int(bot_id))
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.ogg"
        dest = dest_dir / filename

        rc, _stdout, stderr = await _run(
            "ffmpeg", "-y", "-i", src.name,
            "-vn", "-ac", "2", "-ar", "48000", "-c:a", "libopus", "-b:a", "96k",
            str(dest),
        )
        if rc != 0:
            log.warning("ffmpeg 변환 실패 rc={} stderr={}", rc, stderr[-500:])
            with suppress(OSError):
                dest.unlink(missing_ok=True)
            raise SoundValidationError("오디오 변환에 실패했습니다. 파일 형식을 확인해주세요.")
        return filename, duration
    finally:
        with suppress(OSError):
            os.unlink(src.name)


def delete_sound_file(filename: str, *, bot_id: int) -> None:
    path = sound_path(filename, bot_id=bot_id)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("음원 파일 삭제 실패 {}: {}", path, exc)
