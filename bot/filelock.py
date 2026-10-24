"""A lock shared between processes, held on a file.

The OS releases it when the holder closes the file or dies, so a crash never
leaves a stuck lock behind. Works on Windows (msvcrt) and Mac/Linux (fcntl).

    lock = FileLock(path)
    if lock.try_acquire(): ... lock.release()
    async with lock.hold(timeout=600): ...       # waits for it, without blocking the event loop
"""
import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path


class LockTimeout(TimeoutError):
    """The lock was still taken when the wait ran out."""


class FileLock:
    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def try_acquire(self) -> bool:
        if self._fh:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh:
            self._fh.close()            # closing the file releases the lock
            self._fh = None

    @asynccontextmanager
    async def hold(self, timeout: float, poll: float = 0.5):
        """Wait up to `timeout` seconds for the lock; yields the seconds waited."""
        start = time.monotonic()
        while not self.try_acquire():
            if time.monotonic() - start > timeout:
                raise LockTimeout(f"{self.path.name} still taken after {timeout:.0f}s")
            await asyncio.sleep(poll)
        try:
            yield time.monotonic() - start
        finally:
            self.release()
