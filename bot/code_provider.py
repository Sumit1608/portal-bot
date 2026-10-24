"""Second-factor code provider.

The rest of the program only ever calls:

    code = await get_code(challenge)

so the way codes are obtained (TOTP today; email/IMAP or SMS later) can be
swapped without touching the login flow.

Implementation: TOTP. The authenticator "setup key" from .env produces the
same 6-digit code as the phone app, from the current time - no human needed.

Rules:
  * never log or print the code (each code is registered with the redactor)
  * never hand out the same code twice - a retry waits for the next 30s window
  * don't hand out a code that expires in under 3 seconds
  * answer_challenge() retries a rejected code exactly once, then gives up
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

import pyotp

from .redact import REDACTOR
from .totp_secret import SecretError, normalize_totp_secret

log = logging.getLogger("bot.2fa")


@dataclass(frozen=True)
class Challenge:
    """What the portal is asking for."""
    kind: str = "totp"      # "totp" = authenticator app code
    attempt: int = 1        # 1 = first try, 2 = retry after a rejected code
    reason: str = "login"   # for the logs: why a code is needed


class CodeProvider(Protocol):
    async def get_code(self, challenge: Challenge) -> str: ...


class CodeProviderError(RuntimeError):
    """No code can be produced (bad or missing secret, unsupported challenge). Final."""


class CodeRejectedError(RuntimeError):
    """The portal rejected two codes in a row. Final - retrying more would risk a lockout."""


class TotpCodeProvider:
    STEP_SECONDS = 30
    MIN_SECONDS_LEFT = 3

    def __init__(self, secret: str | None, *, clock=time.time, sleep=asyncio.sleep):
        try:
            clean = normalize_totp_secret(secret)
        except SecretError as e:
            raise CodeProviderError(f"GITHUB_TOTP_SECRET: {e}") from None
        REDACTOR.add(clean)
        if secret:
            REDACTOR.add(secret.strip())
        self._totp = pyotp.TOTP(clean)
        self._clock = clock
        self._sleep = sleep
        self._last_window: int | None = None

    async def get_code(self, challenge: Challenge) -> str:
        if challenge.kind != "totp":
            raise CodeProviderError(f"TOTP provider cannot answer a '{challenge.kind}' challenge")

        now = self._clock()
        window = int(now // self.STEP_SECONDS)
        seconds_left = self.STEP_SECONDS - (now % self.STEP_SECONDS)

        wait = 0.0
        if window == self._last_window:
            wait = seconds_left            # this window's code was already used
        elif seconds_left < self.MIN_SECONDS_LEFT:
            wait = seconds_left            # would expire before the portal checks it
        if wait:
            log.info("2FA: waiting %.1fs for a fresh code window (attempt %d)",
                     wait, challenge.attempt)
            await self._sleep(wait + 0.2)
            now = self._clock()
            window = int(now // self.STEP_SECONDS)

        code = self._totp.at(now)
        REDACTOR.add(code, min_length=6)   # from now on it can never appear in a log
        self._last_window = window
        log.info("2FA: code generated for %s (attempt %d) - value not logged",
                 challenge.reason, challenge.attempt)
        return code


# ---- the single entry point the rest of the program uses -------------------

_default_provider: CodeProvider | None = None


def provider_from_env() -> TotpCodeProvider:
    from dotenv import load_dotenv
    load_dotenv()
    return TotpCodeProvider(os.getenv("GITHUB_TOTP_SECRET"))


async def get_code(challenge: Challenge) -> str:
    global _default_provider
    if _default_provider is None:
        _default_provider = provider_from_env()
    return await _default_provider.get_code(challenge)


async def answer_challenge(
    submit: Callable[[str], Awaitable[bool]],
    *,
    provider: CodeProvider | None = None,
    reason: str = "login",
) -> int:
    """Get a code and submit it. If the portal rejects it, retry ONCE with a
    fresh code. Returns the attempt number that succeeded (1 or 2).

    `submit(code)` types the code into the page and returns True if accepted.
    """
    for attempt in (1, 2):
        challenge = Challenge(kind="totp", attempt=attempt, reason=reason)
        code = await (provider.get_code(challenge) if provider else get_code(challenge))
        if await submit(code):
            log.info("2FA: accepted on attempt %d", attempt)
            return attempt
        log.warning("2FA: code rejected on attempt %d", attempt)
    raise CodeRejectedError("2FA code rejected twice - check GITHUB_TOTP_SECRET "
                            "and that the computer clock is synced")
