"""The saved login session (cookies) on disk: state/github.json.

Checks done here are OFFLINE (no browser): is the file there, readable,
and does it hold a GitHub session cookie that has not expired? The live
check - does GitHub still accept it? - is done by GitHubAuth.probe().
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

log = logging.getLogger("bot.session")
SESSION_COOKIE = "user_session"


class SessionStore:
    def __init__(self, path: Path):
        self.path = path

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
            log.info("Session: saved session deleted (--fresh)")

    def usable_state(self, now: float | None = None) -> tuple[Path | None, str]:
        """Return (path, reason). path is None when the saved state can't be used."""
        if not self.path.exists():
            return None, "no saved session"
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            cookies = data["cookies"]
            if not isinstance(cookies, list):
                raise TypeError("cookies is not a list")
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self.quarantine()
            return None, "saved session file is corrupt"

        now = time.time() if now is None else now
        session = [c for c in cookies
                   if isinstance(c, dict) and c.get("name") == SESSION_COOKIE
                   and "github.com" in str(c.get("domain", ""))]
        if not session:
            return None, "saved session has no GitHub session cookie"
        expires = session[0].get("expires", -1)
        if isinstance(expires, (int, float)) and expires != -1 and expires < now:
            return None, "session cookie has expired"
        return self.path, "saved session found"

    def quarantine(self) -> None:
        """Keep a damaged file for inspection instead of silently deleting it."""
        if self.path.exists():
            target = self.path.with_name(self.path.name + ".corrupt")
            os.replace(self.path, target)
            log.warning("Session: unreadable state file moved to %s", target.name)

    async def save(self, context: BrowserContext) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        await context.storage_state(path=str(tmp))
        os.replace(tmp, self.path)          # atomic: never a half-written file
        log.info("Session: saved for the next run")
