"""Check that the TOTP setup key in .env matches your authenticator app.

The key and the generated code are never printed. You type the code from
your phone (hidden as you type) and the script only says MATCH or NO MATCH.
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyotp  # noqa: E402

from bot.config import load_settings  # noqa: E402
from bot.totp_secret import SecretError, normalize_totp_secret  # noqa: E402

try:
    secret = normalize_totp_secret(load_settings().totp_secret)
except SecretError as e:
    sys.exit(f"Problem with GITHUB_TOTP_SECRET: {e}")

print(f"Setup key format OK ({len(secret)} characters).")
typed = getpass.getpass("Type the 6-digit code from your authenticator app: ").strip()
if pyotp.TOTP(secret).verify(typed, valid_window=1):
    print("MATCH - the setup key in .env is correct.")
else:
    print("NO MATCH - the key is well-formed but belongs to a different setup, "
          "or your computer clock is off. See the README troubleshooting.")
    sys.exit(1)
