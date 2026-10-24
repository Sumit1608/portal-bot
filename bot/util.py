"""Small helpers shared by several modules."""
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar
from urllib.parse import urlsplit, urlunsplit

T = TypeVar("T")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def without_query(url: str) -> str:
    """Drop the query string and fragment: they can carry tokens."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def retry_busy(action: Callable[[], T], tries: int = 25) -> T:
    """Windows briefly refuses to open a file that is being replaced, or to
    replace one that someone is reading (PermissionError). Try again a few
    times; any other error (e.g. FileNotFoundError) is passed on."""
    for attempt in range(tries):
        try:
            return action()
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(0.02)
    raise AssertionError("unreachable")


def write_atomic(path: Path, text: str) -> None:
    """Write through a temporary file and rename it, so readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    retry_busy(lambda: os.replace(tmp, path))
