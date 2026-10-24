"""Keep secrets out of logs and evidence.

Every sensitive value (password, TOTP secret, each generated 2FA code) is
registered here the moment it is known. Every log line passes through
RedactingFilter, which replaces registered values with [REDACTED] - so even
an accidental log call cannot leak them.
"""
import logging
import re
import threading

MASK = "[REDACTED]"


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
