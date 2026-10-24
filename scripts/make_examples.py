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
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import ROOT, load_settings  # noqa: E402
from bot.redact import clean  # noqa: E402
from bot.secrets_scan import scan_path  # noqa: E402

TEXT = {".json", ".log", ".txt", ".html", ".md"}

p = argparse.ArgumentParser()
p.add_argument("--success", default="output/eval-1-fresh")
p.add_argument("--failure", default="output/eval-6-broken")
args = p.parse_args()

settings = load_settings()
settings.register_with_redactor()

problems = 0
for source_arg, name in ((args.success, "successful-run"), (args.failure, "failure-bundle")):
    source = ROOT / source_arg
    if not (source / "manifest.json").exists():
        sys.exit(f"{source_arg} has no manifest.json - run python evaluate.py first")
    target = ROOT / "examples" / name
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target)
    for file in target.rglob("*"):
        if file.is_file() and file.suffix.lower() in TEXT:
            text = file.read_text(encoding="utf-8", errors="replace")
            file.write_text(clean(text, settings.identifiers), encoding="utf-8")
    leaks = [hit for f in target.iterdir() if f.name != "documents" for hit in scan_path(f, settings.secrets())]
    for file, label in leaks:
        print(f"FOUND {label} in {file.relative_to(ROOT)}")
    problems += len(leaks)
    print(f"{name}: copied from {source_arg}")

print("OK - examples ready to commit." if not problems else f"{problems} problem(s) - do not commit.")
sys.exit(1 if problems else 0)
