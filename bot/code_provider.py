"""Second-factor code provider.

The login flow only ever calls

    code = await provider.get_code(challenge)

so the way codes are obtained (TOTP today; email/IMAP or SMS later) can be
swapped without touching the login flow.

Implementation: TOTP. The authenticator "setup key" from .env produces the
same 6-digit code as the phone app, from the current time - no human needed.

Rules:
  * never log or print the code (each code is registered with the redactor)
  * never hand out the same code twice - a retry waits for the next 30s window
    (shared between processes through a small file when jobs run in parallel)
  * don't hand out a code that expires in under 3 seconds
  * answer_challenge() retries a rejected code exactly once, then gives up
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol

import pyotp

from .errors import FinalError
from .redact import REDACTOR
from .totp_secret import SecretError, normalize_totp_secret
from .util import write_atomic

log = logging.getLogger("bot.2fa")


@dataclass(frozen=True)
class Challenge:
    """What the portal is asking for."""
    kind: str = "totp"      # "totp" = authenticator app code
    attempt: int = 1        # 1 = first try, 2 = retry after a rejected code
    reason: str = "login"   # for the logs: why a code is needed


class CodeProvider(Protocol):
    async def get_code(self, challenge: Challenge) -> str: ...


class CodeProviderError(FinalError):
    """No code can be produced (bad or missing secret, unsupported challenge)."""


class CodeRejectedError(FinalError):
    """The portal rejected two codes in a row - retrying more would risk a lockout."""


class TotpCodeProvider:
    STEP_SECONDS = 30
    MIN_SECONDS_LEFT = 3

    def __init__(self, secret: str | None, *, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 window_file: Path | None = None):
        """clock: current time, corrected to the portal's clock if needed.
        window_file: shares the last used window between processes; it holds
        only a time-window number, never a code."""
        try:
            clean = normalize_totp_secret(secret)
        except SecretError as e:
            raise CodeProviderError(f"GITHUB_TOTP_SECRET: {e}") from None
        REDACTOR.add(clean)
        REDACTOR.add((secret or "").strip())
        self._totp = pyotp.TOTP(clean)
        self._clock = clock
        self._sleep = sleep
        self._window_file = window_file
        self._last_window: int | None = self._shared_window()

    def _shared_window(self) -> int | None:
        if not self._window_file:
            return None
        try:
            return int(self._window_file.read_text().strip())
        except (OSError, ValueError):
            return None

    def _remember_window(self, window: int) -> None:
        self._last_window = window
        if self._window_file:
            write_atomic(self._window_file, str(window))

    async def get_code(self, challenge: Challenge) -> str:
        if challenge.kind != "totp":
            raise CodeProviderError(f"TOTP provider cannot answer a '{challenge.kind}' challenge")

        now = self._clock()
        window = int(now // self.STEP_SECONDS)
        seconds_left = self.STEP_SECONDS - (now % self.STEP_SECONDS)

        shared = self._shared_window()          # another process may have used a code since
        if shared is not None and (self._last_window is None or shared > self._last_window):
            self._last_window = shared
        # this window's code was already used, or it would expire before the portal checks it
        if window == self._last_window or seconds_left < self.MIN_SECONDS_LEFT:
            log.info("2FA: waiting %.1fs for a fresh code window (attempt %d)",
                     seconds_left, challenge.attempt)
            await self._sleep(seconds_left + 0.2)
            now = self._clock()
            window = int(now // self.STEP_SECONDS)

        code = self._totp.at(now)
        REDACTOR.add(code, min_length=6)   # from now on it can never appear in a log
        self._remember_window(window)
        log.info("2FA: code generated for %s (attempt %d) - value not logged",
                 challenge.reason, challenge.attempt)
        return code


async def answer_challenge(submit: Callable[[str], Awaitable[bool]], provider: CodeProvider,
                           reason: str = "login") -> int:
    """Get a code and submit it. If the portal rejects it, retry ONCE with a
    fresh code. Returns the attempt number that succeeded (1 or 2).

    `submit(code)` types the code into the page and returns True if accepted.
    """
    for attempt in (1, 2):
        code = await provider.get_code(Challenge(kind="totp", attempt=attempt, reason=reason))
        if await submit(code):
            log.info("2FA: accepted on attempt %d", attempt)
            return attempt
        log.warning("2FA: code rejected on attempt %d", attempt)
    raise CodeRejectedError("2FA code rejected twice - check GITHUB_TOTP_SECRET "
                            "and that the computer clock is synced")
