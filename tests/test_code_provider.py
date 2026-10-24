"""Offline tests for the 2FA code provider. No account or internet needed.

    python -m pytest -q
"""
import asyncio
import logging

import pyotp
import pytest

from bot.code_provider import (Challenge, CodeProviderError, CodeRejectedError,
                               TotpCodeProvider, answer_challenge)
from bot.log import setup_logging

SECRET = "JBSWY3DPEHPK3PXP"          # public example key from TOTP docs, not a real account
WINDOW = 30 * 40_000_000             # start of some 30-second window


class FakeTime:
    """A clock we control, so tests never really wait."""
    def __init__(self, t: float):
        self.t = t
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def make(t: float):
    ft = FakeTime(t)
    return TotpCodeProvider(SECRET, clock=ft.clock, sleep=ft.sleep), ft


def run(coro):
    return asyncio.run(coro)


def test_code_matches_authenticator_app():
    provider, ft = make(WINDOW + 5)
    code = run(provider.get_code(Challenge()))
    assert code == pyotp.TOTP(SECRET).at(WINDOW + 5)
    assert len(code) == 6 and code.isdigit()
    assert ft.slept == []


def test_same_window_is_never_used_twice():
    provider, ft = make(WINDOW + 5)
    first = run(provider.get_code(Challenge(attempt=1)))
    second = run(provider.get_code(Challenge(attempt=2)))
    assert ft.slept and ft.slept[0] >= 25           # waited for the next window
    assert int(ft.t // 30) == WINDOW // 30 + 1
    assert second == pyotp.TOTP(SECRET).at(ft.t)
    assert first != second


def test_waits_if_code_about_to_expire():
    provider, ft = make(WINDOW + 28.5)              # 1.5 s left in this window
    run(provider.get_code(Challenge()))
    assert ft.slept and ft.slept[0] < 3


def test_unsupported_challenge_is_refused():
    provider, _ = make(WINDOW)
    with pytest.raises(CodeProviderError):
        run(provider.get_code(Challenge(kind="sms")))


def test_bad_secret_is_refused():
    with pytest.raises(CodeProviderError):
        TotpCodeProvider("not a key!")


def test_wrong_code_is_retried_once():
    provider, _ = make(WINDOW + 5)
    submitted = []

    async def portal(code):              # rejects the first code, accepts the second
        submitted.append(code)
        return len(submitted) == 2

    assert run(answer_challenge(portal, provider=provider)) == 2
    assert len(submitted) == 2 and submitted[0] != submitted[1]


def test_two_rejections_is_final():
    provider, _ = make(WINDOW + 5)
    calls = []

    async def portal(code):
        calls.append(code)
        return False

    with pytest.raises(CodeRejectedError):
        run(answer_challenge(portal, provider=provider))
    assert len(calls) == 2               # exactly one retry, never more


def test_code_and_secret_never_reach_the_log(tmp_path, capsys):
    log_file = tmp_path / "run.log"
    setup_logging(log_file)
    provider, _ = make(WINDOW + 5)
    seen = []

    async def careless_portal(code):     # simulates a bug that tries to log secrets
        seen.append(code)
        logging.getLogger("bot.test").info("typed %s using key %s", code, SECRET)
        return True

    run(answer_challenge(careless_portal, provider=provider))
    for handler in logging.getLogger("bot").handlers:
        handler.flush()

    text = log_file.read_text(encoding="utf-8") + capsys.readouterr().err
    assert seen[0] not in text
    assert SECRET not in text
    assert "[REDACTED]" in text
