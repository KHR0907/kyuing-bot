import pytest

import worker_lock


def test_second_worker_for_same_bot_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_lock, "DATABASE_PATH", str(tmp_path / "bot.db"))
    first = worker_lock.WorkerFileLock(7)
    second = worker_lock.WorkerFileLock(7)
    first.acquire()
    try:
        with pytest.raises(worker_lock.WorkerAlreadyRunning):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()
