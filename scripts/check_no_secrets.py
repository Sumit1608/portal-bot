"""Scan files for anything that must never be published.

    python scripts/check_no_secrets.py fixtures examples

Looks for the values from .env (password, 2FA setup key, username, REDACT_EXTRA)
in plain text, lower/upper case, URL-encoded and base64-encoded form.
Prints only WHERE something was found, never the value itself.
Exit code 1 if anything is found.
"""
import base64
import binascii
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

secrets = {
    "password": os.getenv("GITHUB_PASSWORD", ""),
    "2FA setup key": (os.getenv("GITHUB_TOTP_SECRET", "") or "").replace(" ", ""),
    "username": os.getenv("GITHUB_USERNAME", ""),
}
for i, extra in enumerate(os.getenv("REDACT_EXTRA", "").split(",")):
    secrets[f"REDACT_EXTRA #{i + 1}"] = extra.strip()
secrets = {k: v for k, v in secrets.items() if len(v) >= 3}


B64 = re.compile(r"[A-Za-z0-9+/]{8,}={0,2}")


def decoded_base64(text: str) -> str:
    """All base64-looking pieces of the file, decoded, joined together."""
    parts = []
    for token in B64.findall(text):
        try:
            parts.append(base64.b64decode(token + "=" * (-len(token) % 4)).decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError):
            pass
    return "\n".join(parts).lower()


folders = [ROOT / a for a in (sys.argv[1:] or ["fixtures"])]
found = 0
for folder in folders:
    files = [folder] if folder.is_file() else [f for f in folder.rglob("*") if f.is_file()]
    for file in files:
        try:
            raw = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text = raw.lower()
        hidden = decoded_base64(raw)
        for label, value in secrets.items():
            v = value.lower()
            if v in text or quote(value).lower() in text or v in hidden:
                print(f"FOUND {label} in {file.relative_to(ROOT)}")
                found += 1
print("OK - nothing sensitive found." if not found else f"{found} problem(s) - do not commit these files.")
sys.exit(1 if found else 0)
