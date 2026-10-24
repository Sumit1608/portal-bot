"""Keep secrets and account names out of logs, saved pages and evidence.

Every sensitive value (password, TOTP secret, each generated 2FA code) is
registered with REDACTOR the moment it is known. Every log line passes through
RedactingFilter, which replaces registered values with [REDACTED] - so even an
accidental log call cannot leak them.

Account identifiers (username, REDACT_EXTRA) are also replaced with a neutral
name in saved pages, including base64-encoded copies: GitHub stores
'owner/repo' base64-encoded in some attributes.
"""
import base64
import binascii
import logging
import re
import threading

MASK = "[REDACTED]"
NEUTRAL_NAME = "octo-user"
B64_ATTRIBUTE = re.compile(r'(?<==")[A-Za-z0-9+/]{8,}={0,2}(?=")')


class Redactor:
    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def add(self, value: str | None, min_length: int = 4) -> None:
        if value and len(value) >= min_length:
            with self._lock:
                self._values.add(value)

    def scrub(self, text: str) -> str:
        with self._lock:
            values = sorted(self._values, key=len, reverse=True)   # longest first
        if not values:
            return text
        pattern = re.compile("|".join(re.escape(v) for v in values), re.IGNORECASE)
        return pattern.sub(MASK, text)


REDACTOR = Redactor()


class RedactingFilter(logging.Filter):
    """Attach to every log handler: rewrites the message with secrets masked."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = REDACTOR.scrub(record.getMessage())
        record.args = ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = REDACTOR.scrub(record.exc_text)
        return True


def _replace_all(text: str, identifiers: list[str]) -> str:
    for ident in identifiers:
        text = re.sub(re.escape(ident), NEUTRAL_NAME, text, flags=re.IGNORECASE)
    return text


def mask_identifiers(text: str, identifiers: list[str]) -> str:
    """Replace account identifiers, plain and inside base64-encoded attribute values."""
    idents = [i for i in identifiers if i and len(i) >= 3]

    def fix_b64(match: re.Match) -> str:
        raw = match.group(0)
        try:
            decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return raw
        cleaned = _replace_all(decoded, idents)
        return raw if cleaned == decoded else base64.b64encode(cleaned.encode()).decode()

    return _replace_all(B64_ATTRIBUTE.sub(fix_b64, text), idents)


def clean(text: str, identifiers: list[str]) -> str:
    """Secrets masked and account names neutralised: safe to write to disk."""
    return mask_identifiers(REDACTOR.scrub(text), identifiers)
