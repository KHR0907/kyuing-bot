"""Cross-process guard preventing two workers from using the same bot token."""

import os
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None
    import msvcrt

from config import DATABASE_PATH


class WorkerAlreadyRunning(RuntimeError):
    pass


class WorkerFileLock:
    def __init__(self, bot_id: int):
        run_dir = Path(DATABASE_PATH).parent / "run"
        self.path = run_dir / f"bot-{int(bot_id)}.lock"
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:  # pragma: no cover - Windows fallback
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except (BlockingIOError, OSError) as exc:
            os.close(fd)
            raise WorkerAlreadyRunning(f"bot worker already running: {self.path.stem}") from exc
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("ascii"))
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        if fcntl is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        else:  # pragma: no cover - Windows fallback
            os.lseek(self._fd, 0, os.SEEK_SET)
            msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
        os.close(self._fd)
        self._fd = None
