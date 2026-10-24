"""Offline tests for the shared helpers: settings, redaction of identifiers, atomic writes, errors."""
import base64

from bot.code_provider import CodeProviderError, CodeRejectedError
from bot.compare_flow import FormInputError, NavigationError
from bot.config import Settings
from bot.documents import DocumentError
from bot.errors import FinalError, RetryableError
from bot.github_login import CaptchaError, LoginError
from bot.inputs import InputError
from bot.redact import NEUTRAL_NAME, mask_identifiers
from bot.resolver import ResolveError
from bot.util import write_atomic

SETTINGS = Settings(username="OctoCat42", password="hunter2-pass", totp_secret="JBSW Y3DP EHPK 3PXP",
                    redact_extra=("Octo Person",))


def test_settings_lists_every_value_to_protect():
    assert SETTINGS.identifiers == ["OctoCat42", "Octo Person"]
    assert SETTINGS.secrets() == {"password": "hunter2-pass", "2FA setup key": "JBSWY3DPEHPK3PXP",
                                  "username": "OctoCat42", "REDACT_EXTRA #1": "Octo Person"}


def test_placeholder_username_is_not_a_credential():
    assert SETTINGS.has_credentials
    assert not Settings("your-username", "x", "", ()).has_credentials
    assert not Settings("someone", "", "", ()).has_credentials


def test_identifiers_are_masked_plain_and_in_base64_attributes():
    hidden = base64.b64encode(b"OctoCat42/portal-bot").decode()
    text = mask_identifiers(f'<a title="octocat42" data-nwo="{hidden}">', ["OctoCat42"])
    assert "octocat42" not in text.lower()
    masked = base64.b64decode(text.split('data-nwo="')[1].split('"')[0]).decode()
    assert masked == f"{NEUTRAL_NAME}/portal-bot"


def test_write_atomic_replaces_whole_file(tmp_path):
    target = tmp_path / "sub" / "a.json"
    write_atomic(target, "one")
    write_atomic(target, "two")
    assert target.read_text() == "two"
    assert [p.name for p in target.parent.iterdir()] == ["a.json"]      # no temp file left


def test_every_bot_error_is_either_final_or_retryable():
    final = (InputError, FormInputError, LoginError, CaptchaError, CodeRejectedError, CodeProviderError)
    retryable = (NavigationError, DocumentError, ResolveError)
    assert all(issubclass(e, FinalError) and not issubclass(e, RetryableError) for e in final)
    assert all(issubclass(e, RetryableError) and not issubclass(e, FinalError) for e in retryable)
