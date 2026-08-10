import asyncio
from collections import defaultdict, deque
import os
import signal
import sys
import time
from pathlib import Path

from loguru import logger as log

import database
from config import LOG_PATH


class BotProcessManager:
    """Dashboard-owned process manager for Discord bot worker subprocesses."""

    def __init__(self, project_dir: str | None = None, *, protected_bot_ids: set[int] | None = None):
        self.project_dir = Path(project_dir or Path(__file__).parent)
        self.processes: dict[int, asyncio.subprocess.Process] = {}
        self._monitor_tasks: dict[int, asyncio.Task] = {}
        self.protected_bot_ids = {int(bot_id) for bot_id in (protected_bot_ids or set())}
        self._operation_locks: dict[int, asyncio.Lock] = {}
        self._restart_history: dict[int, deque[float]] = defaultdict(deque)
        self._intentional_stops: set[int] = set()
        self._shutting_down = False

    def is_protected(self, bot_id: int) -> bool:
        """Return True for bots owned by the parent process, not this manager."""
        return int(bot_id) in self.protected_bot_ids

    def _operation_lock(self, bot_id: int) -> asyncio.Lock:
        return self._operation_locks.setdefault(int(bot_id), asyncio.Lock())

    def is_running(self, bot_id: int) -> bool:
        proc = self.processes.get(int(bot_id))
        return proc is not None and proc.returncode is None

    async def start_bot(self, bot_id: int) -> bool:
        bot_id = int(bot_id)
        if self.is_protected(bot_id):
            log.warning("protected bot start rejected bot_id={}", bot_id)
            return False

        async with self._operation_lock(bot_id):
            return await self._start_bot_unlocked(bot_id)

    async def _start_bot_unlocked(self, bot_id: int) -> bool:
        if self.is_running(bot_id):
            return True

        bot_record = await database.get_bot(bot_id)
        if (
            not bot_record
            or not bot_record.get("enabled")
            or bot_record.get("desired_state") != "running"
        ):
            return False

        env = os.environ.copy()
        env["KYUING_BOT_ID"] = str(bot_id)
        log_path = Path(LOG_PATH)
        env["LOG_PATH"] = str(log_path.with_name(f"{log_path.stem}.bot-{bot_id}{log_path.suffix}"))
        # Worker reads token from DB, not argv, so it does not appear in ps output.
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "bot.py",
            "--worker",
            "--bot-id",
            str(bot_id),
            cwd=str(self.project_dir),
            env=env,
        )
        self.processes[bot_id] = proc
        await database.update_bot_runtime_status(bot_id, "starting", pid=proc.pid)
        self._monitor_tasks[bot_id] = asyncio.create_task(self._monitor(bot_id, proc))
        log.info("bot worker started bot_id={} pid={}", bot_id, proc.pid)
        return True

    async def stop_bot(self, bot_id: int) -> bool:
        bot_id = int(bot_id)
        if self.is_protected(bot_id):
            log.warning("protected bot stop rejected bot_id={}", bot_id)
            return False

        async with self._operation_lock(bot_id):
            return await self._stop_bot_unlocked(bot_id)

    async def _stop_bot_unlocked(self, bot_id: int) -> bool:
        proc = self.processes.get(bot_id)
        if proc is None or proc.returncode is not None:
            await database.update_bot_runtime_status(bot_id, "stopped", pid=None)
            self.processes.pop(bot_id, None)
            return True

        self._intentional_stops.add(bot_id)
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=20)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

        self.processes.pop(bot_id, None)
        task = self._monitor_tasks.pop(bot_id, None)
        if task:
            task.cancel()
        self._intentional_stops.discard(bot_id)
        await database.update_bot_runtime_status(bot_id, "stopped", pid=None)
        log.info("bot worker stopped bot_id={}", bot_id)
        return True

    async def restart_bot(self, bot_id: int) -> bool:
        bot_id = int(bot_id)
        if self.is_protected(bot_id):
            log.warning("protected bot restart rejected bot_id={}", bot_id)
            return False
        async with self._operation_lock(bot_id):
            await self._stop_bot_unlocked(bot_id)
            return await self._start_bot_unlocked(bot_id)

    async def start_enabled_bots(self, exclude: set[int] | None = None):
        self._shutting_down = False
        exclude = set(exclude or set()) | self.protected_bot_ids
        for bot in await database.get_enabled_bots():
            if bot["id"] in exclude:
                continue
            await self.start_bot(bot["id"])

    async def stop_all(self):
        self._shutting_down = True
        for bot_id in list(self.processes):
            await self.stop_bot(bot_id)

    async def _monitor(self, bot_id: int, proc: asyncio.subprocess.Process):
        rc = await proc.wait()
        if self.processes.get(bot_id) is proc:
            self.processes.pop(bot_id, None)
        if self._monitor_tasks.get(bot_id) is asyncio.current_task():
            self._monitor_tasks.pop(bot_id, None)
        if rc == 0:
            await database.update_bot_runtime_status(bot_id, "stopped", pid=None)
        elif rc < 0:
            sig = signal.Signals(-rc).name if -rc in [s.value for s in signal.Signals] else f"SIG{-rc}"
            await database.update_bot_runtime_status(bot_id, "stopped", pid=None, last_error=f"terminated by {sig}")
        else:
            await database.update_bot_runtime_status(bot_id, "error", pid=None, last_error=f"exit code {rc}")
        log.warning("bot worker exited bot_id={} rc={}", bot_id, rc)

        if bot_id in self._intentional_stops:
            self._intentional_stops.discard(bot_id)
            return
        if self._shutting_down:
            return
        bot_record = await database.get_bot(bot_id)
        if not bot_record or not bot_record["enabled"] or bot_record["desired_state"] != "running":
            return

        now = time.monotonic()
        history = self._restart_history[bot_id]
        while history and now - history[0] > 600:
            history.popleft()
        history.append(now)
        if len(history) > 5:
            await database.update_bot_runtime_status(
                bot_id, "error", pid=None,
                last_error="automatic restart paused after 5 failures in 10 minutes",
            )
            log.error("bot worker restart circuit open bot_id={}", bot_id)
            return
        delay = (1, 2, 5, 10, 30)[min(len(history) - 1, 4)]
        await asyncio.sleep(delay)
        if not self._shutting_down:
            await self.start_bot(bot_id)
