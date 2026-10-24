"""Find sensitive values in files: plain, URL-encoded or base64-encoded.

Used by scripts/check_no_secrets.py and automatically on every evidence bundle.
Reports only where something was found, never the value.
"""
import base64
import binascii
import os
import re
from pathlib import Path
from urllib.parse import quote

B64 = re.compile(r"[A-Za-z0-9+/]{8,}={0,2}")
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip"}


def load_secrets() -> dict[str, str]:
    """Values from the environment (.env must already be loaded)."""
    found = {
        "password": os.getenv("GITHUB_PASSWORD", ""),
        "2FA setup key": (os.getenv("GITHUB_TOTP_SECRET", "") or "").replace(" ", ""),
        "username": os.getenv("GITHUB_USERNAME", ""),
    }
    for i, extra in enumerate(os.getenv("REDACT_EXTRA", "").split(",")):
        found[f"REDACT_EXTRA #{i + 1}"] = extra.strip()
    return {k: v for k, v in found.items() if len(v) >= 3}


def _decoded_base64(text: str) -> str:
    parts = []
    for token in B64.findall(text):
        try:
            parts.append(base64.b64decode(token + "=" * (-len(token) % 4)).decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError):
            pass
    return "\n".join(parts).lower()


def scan_text(text: str, secrets: dict[str, str]) -> list[str]:
    lower, hidden = text.lower(), _decoded_base64(text)
    return [label for label, value in secrets.items()
            if value.lower() in lower or quote(value).lower() in lower or value.lower() in hidden]


def scan_path(path: Path, secrets: dict[str, str]) -> list[tuple[Path, str]]:
    files = [path] if path.is_file() else sorted(f for f in path.rglob("*") if f.is_file())
    problems = []
    for file in files:
        if file.suffix.lower() in BINARY:
            continue
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        problems += [(file, label) for label in scan_text(text, secrets)]
    return problems
