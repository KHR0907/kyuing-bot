"""Dashboard and worker-process supervisor entry point."""

import asyncio
from contextlib import suppress
import signal

from loguru import logger as log

import config
import database
from bot_process_manager import BotProcessManager
from dashboard_context import DashboardContext
from logging_setup import configure_logging
from web.app import create_app


async def _dashboard_context_refresh_loop(context: DashboardContext, interval: float = 2.0):
    while True:
        await context.refresh_guilds()
        await asyncio.sleep(interval)


async def main():
    config.validate_runtime_config()
    configure_logging()
    await database.init_db()
    context = DashboardContext(bot_id=database.current_bot_id())
    await context.refresh_guilds()
    app = create_app(context)
    manager = BotProcessManager()
    app.bot_process_manager = manager

    refresh_task = None
    web_task = None
    stop_task = None
    try:
        await manager.start_enabled_bots()
        refresh_task = asyncio.create_task(
            _dashboard_context_refresh_loop(context), name="dashboard-context-refresh"
        )
        web_task = asyncio.create_task(
            app.run_task(host="0.0.0.0", port=config.WEB_PORT), name="dashboard-web-server"
        )
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop_event.set)
        stop_task = asyncio.create_task(stop_event.wait(), name="supervisor-stop-signal")
        done, _pending = await asyncio.wait(
            {web_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if web_task in done:
            await web_task
    finally:
        if stop_task is not None:
            stop_task.cancel()
            with suppress(asyncio.CancelledError):
                await stop_task
        if refresh_task is not None:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task
        if web_task is not None and not web_task.done():
            web_task.cancel()
            with suppress(asyncio.CancelledError):
                await web_task
        await manager.stop_all()
        await database.close_db()
        log.info("supervisor stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
