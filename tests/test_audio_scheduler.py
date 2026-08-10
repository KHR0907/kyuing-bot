import asyncio

import pytest

from audio_scheduler import AudioCooldown, AudioQueueFull, GuildAudioScheduler


@pytest.mark.asyncio
async def test_scheduler_runs_jobs_in_fifo_order():
    scheduler = GuildAudioScheduler(maxsize=3, max_per_user=3)
    gate = asyncio.Event()
    calls = []

    async def first():
        calls.append("first-start")
        await gate.wait()
        calls.append("first-end")

    async def second():
        calls.append("second")

    one = asyncio.create_task(scheduler.run(guild_id=1, user_id=10, runner=first))
    await asyncio.sleep(0)
    two = asyncio.create_task(scheduler.run(guild_id=1, user_id=11, runner=second))
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(one, two)
    assert calls == ["first-start", "first-end", "second"]


@pytest.mark.asyncio
async def test_scheduler_rejects_overflow_and_per_user_spam():
    scheduler = GuildAudioScheduler(maxsize=1, max_per_user=1)
    gate = asyncio.Event()

    async def blocked():
        await gate.wait()

    running = asyncio.create_task(scheduler.run(guild_id=1, user_id=10, runner=blocked))
    await asyncio.sleep(0)
    with pytest.raises(AudioQueueFull):
        await scheduler.run(guild_id=1, user_id=10, runner=blocked)
    gate.set()
    await running


@pytest.mark.asyncio
async def test_scheduler_enforces_cooldown():
    now = [100.0]
    scheduler = GuildAudioScheduler(clock=lambda: now[0])

    async def done():
        return None

    await scheduler.run(guild_id=1, user_id=10, runner=done, cooldown_seconds=2)
    now[0] = 101.0
    with pytest.raises(AudioCooldown):
        await scheduler.run(guild_id=1, user_id=10, runner=done, cooldown_seconds=2)
