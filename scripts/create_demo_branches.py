"""Run ONCE by the repo owner: creates two branches on GitHub so the
Compare page has two different comparisons (= two different documents).

    python scripts/create_demo_branches.py

Reviewers do not need this - they use the branches already in the public repo.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BRANCHES = {
    "demo-feature-a": ("demo/feature-a.md",
                       "# Feature A\n\nDemo change used by inputs/example.json.\n\n"
                       "- adds a short list\n- touches one new file\n"),
    "demo-feature-b": ("demo/feature-b.md",
                       "# Feature B\n\nA different demo change, used by inputs/example2.json.\n\n"
                       "| item | value |\n|---|---|\n| colour | blue |\n| size | large |\n"),
}


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def main() -> None:
    current = git("rev-parse", "--abbrev-ref", "HEAD")
    if git("status", "--porcelain"):
        sys.exit("You have uncommitted changes. Commit and push them first, then run this again.")

    remote = git("ls-remote", "--heads", "origin")
    try:
        for branch, (rel_path, content) in BRANCHES.items():
            if f"refs/heads/{branch}" in remote:
                print(f"{branch}: already on GitHub - skipped")
                continue
            git("checkout", "-B", branch, current)        # new branch from main
            file = ROOT / rel_path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(content, encoding="utf-8")
            git("add", rel_path)
            git("commit", "-m", f"Demo change for {branch}")
            git("push", "-u", "origin", branch)
            print(f"{branch}: created and pushed")
    finally:
        git("checkout", current)                           # always return to main
    print(f"Done. You are back on '{current}'.")


if __name__ == "__main__":
    main()
