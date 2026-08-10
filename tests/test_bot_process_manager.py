import pytest

from bot_process_manager import BotProcessManager


@pytest.mark.asyncio
async def test_protected_primary_bot_cannot_be_started_stopped_or_restarted():
    manager = BotProcessManager(protected_bot_ids={1})

    assert manager.is_protected(1)
    assert await manager.start_bot(1) is False
    assert await manager.stop_bot(1) is False
    assert await manager.restart_bot(1) is False
    assert manager.processes == {}


def test_worker_bot_is_not_protected():
    manager = BotProcessManager(protected_bot_ids={1})
    assert manager.is_protected(2) is False
