"""Bounded, per-guild audio scheduler shared by TTS and soundboard jobs."""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Awaitable, Callable

from loguru import logger as log

from config import (
    AUDIO_JOB_TIMEOUT_SECONDS,
    AUDIO_QUEUE_JOB_TTL_SECONDS,
    AUDIO_QUEUE_MAX_PER_USER,
    AUDIO_QUEUE_MAXSIZE,
)


class AudioQueueFull(Exception):
    pass


class AudioCooldown(Exception):
    pass


AudioRunner = Callable[[], Awaitable[str | None]]


@dataclass(slots=True)
class AudioJob:
    guild_id: int
    user_id: int
    runner: AudioRunner
    future: asyncio.Future
    expires_at: float
    timeout_seconds: float


class GuildAudioScheduler:
    def __init__(
        self,
        *,
        maxsize: int = AUDIO_QUEUE_MAXSIZE,
        max_per_user: int = AUDIO_QUEUE_MAX_PER_USER,
        ttl_seconds: float = AUDIO_QUEUE_JOB_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.maxsize = max(1, int(maxsize))
        self.max_per_user = max(1, int(max_per_user))
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self._clock = clock
        self._queues: dict[int, asyncio.Queue[AudioJob]] = {}
        self._workers: dict[int, asyncio.Task] = {}
        self._pending_by_user: dict[tuple[int, int], int] = defaultdict(int)
        self._last_accepted: dict[tuple[int, int], float] = {}

    def pending_count(self, guild_id: int) -> int:
        queue = self._queues.get(int(guild_id))
        return queue.qsize() if queue else 0

    async def run(
        self,
        *,
        guild_id: int,
        user_id: int,
        runner: AudioRunner,
        cooldown_seconds: float = 0,
        timeout_seconds: float = AUDIO_JOB_TIMEOUT_SECONDS,
    ) -> str | None:
        guild_id = int(guild_id)
        user_id = int(user_id)
        key = (guild_id, user_id)
        now = self._clock()
        last_accepted = self._last_accepted.get(key)
        if cooldown_seconds > 0 and last_accepted is not None and now - last_accepted < cooldown_seconds:
            raise AudioCooldown
        if self._pending_by_user[key] >= self.max_per_user:
            raise AudioQueueFull

        queue = self._queues.setdefault(guild_id, asyncio.Queue(maxsize=self.maxsize))
        if queue.full():
            raise AudioQueueFull

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        job = AudioJob(
            guild_id=guild_id,
            user_id=user_id,
            runner=runner,
            future=future,
            expires_at=now + self.ttl_seconds,
            timeout_seconds=max(1.0, float(timeout_seconds)),
        )
        queue.put_nowait(job)
        self._pending_by_user[key] += 1
        self._last_accepted[key] = now
        if guild_id not in self._workers or self._workers[guild_id].done():
            self._workers[guild_id] = asyncio.create_task(
                self._consume(guild_id), name=f"audio-queue-{guild_id}"
            )
        return await future

    async def _consume(self, guild_id: int) -> None:
        queue = self._queues[guild_id]
        while True:
            job = await queue.get()
            key = (job.guild_id, job.user_id)
            try:
                if job.future.cancelled():
                    continue
                if self._clock() > job.expires_at:
                    result = "대기 시간이 너무 길어 음성 작업을 취소했습니다."
                else:
                    try:
                        result = await asyncio.wait_for(job.runner(), timeout=job.timeout_seconds)
                    except asyncio.TimeoutError:
                        result = "음성 작업 시간이 초과되었습니다."
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        log.exception(
                            "audio job failed guild_id={} user_id={}", job.guild_id, job.user_id
                        )
                        result = f"음성 작업 오류: {exc}"
                if not job.future.done():
                    job.future.set_result(result)
            finally:
                self._pending_by_user[key] = max(0, self._pending_by_user[key] - 1)
                queue.task_done()
            if queue.empty():
                self._workers.pop(guild_id, None)
                return

    def clear_guild(self, guild_id: int) -> int:
        queue = self._queues.get(int(guild_id))
        if queue is None:
            return 0
        cleared = 0
        while True:
            try:
                job = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            key = (job.guild_id, job.user_id)
            self._pending_by_user[key] = max(0, self._pending_by_user[key] - 1)
            if not job.future.done():
                job.future.set_result("대기 중이던 음성 작업이 취소되었습니다.")
            queue.task_done()
            cleared += 1
        return cleared


audio_scheduler = GuildAudioScheduler()
