"""Offline tests: which failures retry, how many times, and the backoff."""
import asyncio

import pytest

from bot import retry as retry_module
from bot.code_provider import CodeRejectedError
from bot.compare_flow import FormInputError, NavigationError
from bot.github_login import CaptchaError, LoginError
from bot.manifest import Manifest
from bot.resolver import ResolveError
from bot.retry import FinalFailure, is_retryable, run_with_retries


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)
    monkeypatch.setattr(retry_module.asyncio, "sleep", fake_sleep)
    return waits


def manifest(tmp_path):
    return Manifest("t", "in.json", {}, tmp_path)


@pytest.mark.parametrize("error, expected", [
    (NavigationError("x"), True),
    (ResolveError("k", []), True),
    (TimeoutError(), True),
    (FormInputError("x"), False),
    (LoginError("x"), False),
    (CaptchaError("x"), False),
    (CodeRejectedError("x"), False),
    (ValueError("unexpected bug"), False),
])
def test_classification(error, expected):
    assert is_retryable(error) is expected


def test_retryable_failure_is_retried_then_succeeds(tmp_path, no_waiting):
    m = manifest(tmp_path)
    calls = []

    async def flaky(attempt):
        calls.append(attempt)
        if attempt < 3:
            raise NavigationError("not yet")
        return "ok"

    assert asyncio.run(run_with_retries("form", flaky, m, attempts=3)) == "ok"
    assert calls == [1, 2, 3]
    assert len(no_waiting) == 2 and no_waiting[1] > no_waiting[0]      # backoff grows
    assert [r["attempt"] for r in m.data["retries"]] == [1, 2]


def test_retries_are_bounded(tmp_path):
    m = manifest(tmp_path)
    calls = []

    async def always_fails(attempt):
        calls.append(attempt)
        raise NavigationError("down")

    with pytest.raises(FinalFailure) as info:
        asyncio.run(run_with_retries("form", always_fails, m, attempts=3))
    assert calls == [1, 2, 3]
    assert info.value.retryable is True and info.value.attempts == 3


def test_final_failure_is_not_retried(tmp_path, no_waiting):
    m = manifest(tmp_path)
    calls = []

    async def bad_input(attempt):
        calls.append(attempt)
        raise FormInputError("no such branch")

    with pytest.raises(FinalFailure) as info:
        asyncio.run(run_with_retries("form", bad_input, m, attempts=3))
    assert calls == [1] and no_waiting == []
    assert info.value.retryable is False
