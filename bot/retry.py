"""Bounded retries with exponential backoff, and which failures deserve one.

RETRYABLE - things that can go right on a second try:
  page or element not loaded in time, a click that did not navigate, an
  element not found (page partially rendered), network errors, a document
  that came back empty.
FINAL - retrying cannot help (or would make things worse):
  bad input file, a branch the portal does not offer, wrong username or
  password, 2FA code rejected twice, a CAPTCHA, missing/invalid 2FA secret.
"""
import asyncio
import logging
import os
import random

from playwright.async_api import Error as PlaywrightError

from .code_provider import CodeProviderError, CodeRejectedError
from .compare_flow import FormInputError, NavigationError
from .documents import DocumentError
from .github_login import LoginError
from .inputs import InputError
from .redact import REDACTOR
from .resolver import ResolveError

log = logging.getLogger("bot.retry")

FINAL = (InputError, FormInputError, LoginError, CodeRejectedError, CodeProviderError)
RETRYABLE = (NavigationError, DocumentError, ResolveError, PlaywrightError, TimeoutError)


def is_retryable(error: BaseException) -> bool:
    if isinstance(error, FINAL):          # checked first: LoginError etc. are never retried
        return False
    return isinstance(error, RETRYABLE)


def first_line(error: BaseException) -> str:
    return REDACTOR.scrub(str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__)


class FinalFailure(Exception):
    """A step failed for good: either a final error, or retries ran out."""

    def __init__(self, step: str, cause: BaseException, attempts: int, retryable: bool):
        self.step = step
        self.cause = cause
        self.attempts = attempts
        self.retryable = retryable
        reason = "retries exhausted" if retryable else "not retryable"
        super().__init__(f"{step} failed after {attempts} attempt(s), {reason}: "
                         f"{type(cause).__name__}: {first_line(cause)}")


async def run_with_retries(step: str, action, manifest, *, attempts: int | None = None,
                           base_delay: float = 2.0, max_delay: float = 20.0):
    """Run `action(attempt)`; retry retryable failures with growing waits (2s, 4s, 8s ...)."""
    attempts = attempts or max(1, int(os.getenv("MAX_ATTEMPTS", "3")))
    for attempt in range(1, attempts + 1):
        manifest.attempt = attempt
        try:
            return await action(attempt)
        except Exception as error:
            retry = is_retryable(error)
            entry = {"step": step, "attempt": attempt, "error_type": type(error).__name__,
                     "error": first_line(error), "retryable": retry}
            manifest.data["retries"].append(entry)
            if not retry:
                log.error("%s: final failure (not retryable) - %s: %s",
                          step, type(error).__name__, first_line(error))
                raise FinalFailure(step, error, attempt, retryable=False) from error
            if attempt == attempts:
                log.error("%s: attempt %d/%d failed - giving up (%s)", step, attempt, attempts,
                          first_line(error))
                raise FinalFailure(step, error, attempt, retryable=True) from error
            delay = min(max_delay, base_delay * 2 ** (attempt - 1)) * random.uniform(0.8, 1.2)
            entry["retry_in_seconds"] = round(delay, 1)
            log.warning("%s: attempt %d/%d failed (%s: %s) - retrying in %.1fs",
                        step, attempt, attempts, type(error).__name__, first_line(error), delay)
            await asyncio.sleep(delay)
        finally:
            manifest.attempt = 1
