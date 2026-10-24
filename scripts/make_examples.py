"""Copy one successful run and one failure bundle into examples/ for the repository.

    python scripts/make_examples.py
    python scripts/make_examples.py --success output/eval-1-fresh --failure output/eval-6-broken

Text files (manifest, run.log, evidence) get the same redaction as evidence:
password, 2FA secret, username and REDACT_EXTRA words are masked, including
base64-encoded copies. The documents (PDF, .patch) are copied unchanged - they
are GitHub's own output for a public repository - so their SHA-256 still
matches the manifest. Finally everything except the documents is scanned.
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from bot.fixtures import mask_identifiers  # noqa: E402
from bot.redact import REDACTOR  # noqa: E402
from bot.secrets_scan import load_secrets, scan_path  # noqa: E402

TEXT = {".json", ".log", ".txt", ".html", ".md"}

p = argparse.ArgumentParser()
p.add_argument("--success", default="output/eval-1-fresh")
p.add_argument("--failure", default="output/eval-6-broken")
args = p.parse_args()

load_dotenv(ROOT / ".env")
identifiers = [os.getenv("GITHUB_USERNAME", "")] + \
              [v.strip() for v in os.getenv("REDACT_EXTRA", "").split(",") if v.strip()]
for value in (os.getenv("GITHUB_PASSWORD"), os.getenv("GITHUB_TOTP_SECRET"), *identifiers):
    REDACTOR.add(value)

examples = ROOT / "examples"
problems = 0
for source_arg, name in ((args.success, "successful-run"), (args.failure, "failure-bundle")):
    source = ROOT / source_arg
    if not (source / "manifest.json").exists():
        sys.exit(f"{source_arg} has no manifest.json - run python evaluate.py first")
    target = examples / name
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target)
    for file in target.rglob("*"):
        if file.is_file() and file.suffix.lower() in TEXT:
            text = file.read_text(encoding="utf-8", errors="replace")
            file.write_text(mask_identifiers(REDACTOR.scrub(text), identifiers), encoding="utf-8")
    checked = [f for f in target.iterdir() if f.name != "documents"]
    leaks = [hit for f in checked for hit in scan_path(f, load_secrets())]
    for file, label in leaks:
        print(f"FOUND {label} in {file.relative_to(ROOT)}")
    problems += len(leaks)
    print(f"{name}: copied from {source_arg}")

print("OK - examples ready to commit." if not problems else f"{problems} problem(s) - do not commit.")
sys.exit(1 if problems else 0)
