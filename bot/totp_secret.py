"""Clean up and check a TOTP setup key, without ever revealing it.

People paste setup keys with spaces, quotes, dashes, invisible characters or
as a full otpauth:// link. This turns all of those into a clean key, or
explains what is wrong (describing the problem, never showing the key).
"""
import base64
import re
import unicodedata
from urllib.parse import parse_qs, urlsplit

VALID = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
RECOVERY_CODE = re.compile(r"^[0-9a-f]{5}-[0-9a-f]{5}$", re.IGNORECASE)


class SecretError(ValueError):
    pass


def normalize_totp_secret(raw: str | None) -> str:
    text = (raw or "").strip().strip("\"'")
    if not text or text == "PASTE_SETUP_KEY_HERE":
        raise SecretError("GITHUB_TOTP_SECRET is empty in .env")

    if RECOVERY_CODE.match(text):
        raise SecretError("This looks like a RECOVERY CODE (xxxxx-xxxxx), not the setup key. "
                          "Use the text behind the 'setup key' link under the QR code.")

    if text.lower().startswith("otpauth://"):                # full link pasted
        values = parse_qs(urlsplit(text).query).get("secret")
        if not values:
            raise SecretError("This is an otpauth:// link but it has no secret= part")
        text = values[0]

    # remove spaces (incl. invisible/non-breaking ones), dashes and padding
    cleaned = "".join(ch for ch in text
                      if not ch.isspace() and unicodedata.category(ch) not in ("Cf", "Zs"))
    cleaned = cleaned.replace("-", "").rstrip("=").upper()

    bad = [ch for ch in cleaned if ch not in VALID]
    if bad:
        kinds = set()
        for ch in bad:
            if ch in "0189":
                kinds.add("digits 0, 1, 8 or 9 (a setup key only uses 2-7 - did you type O as 0 or I as 1?)")
            elif ch.isalnum():
                kinds.add("non-English letters")
            else:
                kinds.add("symbols or punctuation")
        raise SecretError(f"The key contains {len(bad)} character(s) that cannot be in a setup key: "
                          + "; ".join(sorted(kinds)))

    if len(cleaned) < 16:
        raise SecretError(f"The key is only {len(cleaned)} characters long; "
                          "GitHub setup keys are usually 16 or 32. Part of it may be missing.")

    try:
        base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    except Exception:
        raise SecretError("The key has a length that cannot be a valid setup key - "
                          "a character was probably lost or added while copying.") from None
    return cleaned
