"""One-command setup. Works on Mac, Windows and Linux:

    python bootstrap.py      (Mac/Linux may need: python3 bootstrap.py)

It creates a private Python environment (.venv), installs the libraries,
downloads the Chromium browser and creates .env from .env.example.
Safe to run again: existing .venv and .env are reused, never overwritten.
"""
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
IS_WINDOWS = os.name == "nt"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def run(cmd: list) -> None:
    print("\n$", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, cwd=ROOT)


def main() -> None:
    if sys.version_info < (3, 11):
        sys.exit(f"Python 3.11+ is required, you have {sys.version.split()[0]}.")

    if venv_python().exists():
        print("Found existing .venv - reusing it.")
    else:
        print("Creating .venv ...")
        venv.EnvBuilder(with_pip=True).create(VENV)

    py = venv_python()
    run([py, "-m", "pip", "install", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "-r", "requirements.txt"])
    run([py, "-m", "playwright", "install", "chromium"])

    env_file = ROOT / ".env"
    if env_file.exists():
        print("\n.env already exists - left unchanged.")
    else:
        shutil.copy(ROOT / ".env.example", env_file)
        print("\nCreated .env - open it and fill in your values.")

    activate = r".venv\Scripts\Activate.ps1" if IS_WINDOWS else "source .venv/bin/activate"
    print("\nSetup complete. Next:")
    print(f"  1. Activate the environment:  {activate}")
    print("  2. Fill in .env")
    print("  3. Run:  python run.py --input inputs/example.json")
    if sys.platform.startswith("linux"):
        print("\nLinux note: if the browser fails to start, run once:")
        print("  sudo .venv/bin/python -m playwright install-deps chromium")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(f"\nSetup failed while running: {' '.join(map(str, e.cmd))}")
