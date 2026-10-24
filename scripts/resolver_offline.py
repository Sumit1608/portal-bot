"""Evaluation scenario 5: run the locator resolver offline against the saved
GitHub pages and mutated copies of them.

    python scripts/resolver_offline.py

Mutated pages and results.json are written to output/resolver-offline/.
"""
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.offline_check import run_offline_check  # noqa: E402

logging.getLogger("bot").addHandler(logging.NullHandler())
logging.getLogger("bot").propagate = False

results = asyncio.run(run_offline_check(ROOT / "fixtures", ROOT / "output" / "resolver-offline"))

print(f"{'page':<18} {'mutation':<20} {'element':<16} result")
print("-" * 72)
for r in results:
    mark = r["strategy"] if r["found"] is not False else "*** " + r["strategy"] + " ***"
    print(f"{r['fixture'].replace('.html', ''):<18} {r['mutation']:<20} {r['target']:<16} {mark}")

failed = [r for r in results if r["found"] is False]
checked = [r for r in results if r["found"] is not None]
print("-" * 72)
print(f"{len(checked) - len(failed)}/{len(checked)} found. "
      f"Mutated pages: output/resolver-offline/")
sys.exit(1 if failed else 0)
