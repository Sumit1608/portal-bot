"""Scan files for anything that must never be published.

    python scripts/check_no_secrets.py fixtures examples

Looks for the values from .env (password, 2FA setup key, username, REDACT_EXTRA)
in plain, URL-encoded and base64-encoded form. Prints only WHERE something was
found, never the value. Images and PDFs are skipped. Exit code 1 if anything is found.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from bot.secrets_scan import load_secrets, scan_path  # noqa: E402

load_dotenv(ROOT / ".env")
secrets = load_secrets()
found = 0
for arg in sys.argv[1:] or ["fixtures"]:
    target = ROOT / arg
    if not target.exists():
        print(f"(skipped {arg}: does not exist)")
        continue
    for file, label in scan_path(target, secrets):
        print(f"FOUND {label} in {file.relative_to(ROOT)}")
        found += 1
print("OK - nothing sensitive found." if not found else f"{found} problem(s) - do not commit these files.")
sys.exit(1 if found else 0)
