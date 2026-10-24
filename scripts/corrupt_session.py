"""Damage the saved session on purpose, to test dead-session detection.

    python scripts/corrupt_session.py            # scramble cookie values (file still valid JSON)
    python scripts/corrupt_session.py --garble   # overwrite the file with junk

'cookies' mode is the realistic one: the file looks fine and the cookie has
not expired, so only the LIVE check can tell GitHub no longer accepts it.
"""
import argparse
import json
import random
import string
import sys
from pathlib import Path

STATE = Path(__file__).resolve().parent.parent / "state" / "github.json"

p = argparse.ArgumentParser()
p.add_argument("--garble", action="store_true", help="overwrite the file with junk instead")
args = p.parse_args()

if not STATE.exists():
    sys.exit("No saved session yet - run python run.py --input inputs/example.json first.")

if args.garble:
    STATE.write_text("{ this is not valid json", encoding="utf-8")
    print("Saved session overwritten with junk.")
else:
    data = json.loads(STATE.read_text(encoding="utf-8"))
    for cookie in data.get("cookies", []):
        cookie["value"] = "".join(random.choices(string.ascii_letters + string.digits,
                                                 k=max(8, len(cookie.get("value", "")))))
    STATE.write_text(json.dumps(data), encoding="utf-8")
    print(f"Scrambled {len(data.get('cookies', []))} cookie values (file is still valid JSON).")
