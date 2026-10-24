"""Offline tests for the cross-process file lock (session lock, worker lock)."""
import asyncio

import pytest

from bot.filelock import FileLock, LockTimeout


def test_only_one_holder(tmp_path):
    a, b = FileLock(tmp_path / "x.lock"), FileLock(tmp_path / "x.lock")
    assert a.try_acquire()
    assert not b.try_acquire()
    a.release()
    assert b.try_acquire()
    b.release()


def test_hold_waits_until_released(tmp_path):
    holder, waiter = FileLock(tmp_path / "x.lock"), FileLock(tmp_path / "x.lock")
    holder.try_acquire()

    async def scenario():
        async def release_later():
            await asyncio.sleep(0.3)
            holder.release()
        asyncio.create_task(release_later())
        async with waiter.hold(timeout=5, poll=0.05) as waited:
            assert waited >= 0.2
        assert holder.try_acquire()               # released again after the block
        holder.release()

    asyncio.run(scenario())


def test_hold_gives_up_after_timeout(tmp_path):
    holder = FileLock(tmp_path / "x.lock")
    holder.try_acquire()

    async def scenario():
        async with FileLock(tmp_path / "x.lock").hold(timeout=0.2, poll=0.05):
            pass

    with pytest.raises(LockTimeout):
        asyncio.run(scenario())
    holder.release()
