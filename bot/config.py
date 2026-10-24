"""The account settings from .env, read in one place.

Every command that touches the account (run.py, the leak scanner, the
examples script) loads them through load_settings(), so the list of values
that must never be published is defined once.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .redact import REDACTOR

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER_USERNAME = "your-username"


@dataclass(frozen=True)
class Settings:
    username: str
    password: str
    totp_secret: str
    redact_extra: tuple[str, ...]

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password and self.username != PLACEHOLDER_USERNAME)

    @property
    def identifiers(self) -> list[str]:
        """Account names to mask in saved pages and evidence (not secret, but private)."""
        return [v for v in (self.username, *self.redact_extra) if v]

    def secrets(self) -> dict[str, str]:
        """Labelled values the leak scanner looks for (the label is printed, never the value)."""
        found = {"password": self.password,
                 "2FA setup key": self.totp_secret.replace(" ", ""),
                 "username": self.username}
        found.update({f"REDACT_EXTRA #{i}": v for i, v in enumerate(self.redact_extra, 1)})
        return {k: v for k, v in found.items() if len(v) >= 3}

    def register_with_redactor(self) -> None:
        """From now on none of these values can appear in a log line."""
        for value in (self.password, self.totp_secret, *self.identifiers):
            REDACTOR.add(value)


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    return Settings(
        username=os.getenv("GITHUB_USERNAME", "").strip(),
        password=os.getenv("GITHUB_PASSWORD", ""),
        totp_secret=os.getenv("GITHUB_TOTP_SECRET", ""),
        redact_extra=tuple(v.strip() for v in os.getenv("REDACT_EXTRA", "").split(",") if v.strip()),
    )
