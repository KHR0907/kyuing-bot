import asyncio
import os
import signal
import sys
from pathlib import Path

from loguru import logger as log

import database


class BotProcessManager:
    """Dashboard-owned process manager for Discord bot worker subprocesses."""

    def __init__(self, project_dir: str | None = None):
        self.project_dir = Path(project_dir or Path(__file__).parent)
        self.processes: dict[int, asyncio.subprocess.Process] = {}
        self._monitor_tasks: dict[int, asyncio.Task] = {}

    def is_running(self, bot_id: int) -> bool:
        proc = self.processes.get(int(bot_id))
        return proc is not None and proc.returncode is None

    async def start_bot(self, bot_id: int) -> bool:
        bot_id = int(bot_id)
        if self.is_running(bot_id):
            return True

        bot_record = await database.get_bot(bot_id)
        if not bot_record or not bot_record.get("enabled"):
            return False

        env = os.environ.copy()
        env["KYUING_BOT_ID"] = str(bot_id)
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
        proc = self.processes.get(bot_id)
        if proc is None or proc.returncode is not None:
            await database.update_bot_runtime_status(bot_id, "stopped", pid=None)
            self.processes.pop(bot_id, None)
            return True

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
        await database.update_bot_runtime_status(bot_id, "stopped", pid=None)
        log.info("bot worker stopped bot_id={}", bot_id)
        return True

    async def restart_bot(self, bot_id: int) -> bool:
        await self.stop_bot(bot_id)
        return await self.start_bot(bot_id)

    async def start_enabled_bots(self, exclude: set[int] | None = None):
        exclude = exclude or set()
        for bot in await database.get_enabled_bots():
            if bot["id"] in exclude:
                continue
            await self.start_bot(bot["id"])

    async def stop_all(self):
        for bot_id in list(self.processes):
            await self.stop_bot(bot_id)

    async def _monitor(self, bot_id: int, proc: asyncio.subprocess.Process):
        rc = await proc.wait()
        self.processes.pop(bot_id, None)
        if rc == 0:
            await database.update_bot_runtime_status(bot_id, "stopped", pid=None)
        elif rc < 0:
            sig = signal.Signals(-rc).name if -rc in [s.value for s in signal.Signals] else f"SIG{-rc}"
            await database.update_bot_runtime_status(bot_id, "stopped", pid=None, last_error=f"terminated by {sig}")
        else:
            await database.update_bot_runtime_status(bot_id, "error", pid=None, last_error=f"exit code {rc}")
        log.warning("bot worker exited bot_id={} rc={}", bot_id, rc)
