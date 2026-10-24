"""Main command.

    python run.py --input inputs/example.json [--headless] [--fresh]

Current version: loads inputs, signs in to GitHub (reusing the saved session
when it is still alive, otherwise a full login with 2FA), opens the
repository page, and writes the manifest and a redacted run.log.
"""
import argparse
import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from bot.github_login import GitHubAuth
from bot.inputs import InputError, load_inputs
from bot.log import setup_logging
from bot.manifest import Manifest
from bot.redact import REDACTOR
from bot.session import SessionStore

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state" / "github.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GitHub portal automation")
    p.add_argument("--input", required=True, help="path to the input JSON file")
    p.add_argument("--headless", action="store_true", help="run without a visible browser window")
    p.add_argument("--fresh", action="store_true", help="delete the saved session first (forces a full login)")
    return p.parse_args()


async def main() -> int:
    args = parse_args()

    t = time.perf_counter()
    try:
        inputs = load_inputs(args.input)
    except InputError as e:
        print(f"Input error: {e}")
        return 2
    load_seconds = time.perf_counter() - t

    load_dotenv()
    username = os.getenv("GITHUB_USERNAME", "").strip()
    password = os.getenv("GITHUB_PASSWORD", "")
    if not username or not password or username == "your-username":
        print("Missing GITHUB_USERNAME or GITHUB_PASSWORD in .env")
        return 2
    for value in (password, os.getenv("GITHUB_TOTP_SECRET")):   # never let these reach a log
        REDACTOR.add(value)

    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{inputs.label}"
    out_dir = ROOT / "output" / run_id
    manifest = Manifest(run_id, args.input, inputs.to_dict(), out_dir)
    manifest.record_stage("load_inputs", load_seconds)
    log = setup_logging(out_dir / "run.log")
    log.info("Run %s started with %s", run_id, args.input)

    store = SessionStore(STATE_FILE)
    if args.fresh:
        store.clear()

    status, error = "failed", None
    try:
        async with async_playwright() as p:
            with manifest.stage("launch_browser"):
                browser = await p.chromium.launch(headless=args.headless)
            try:
                auth = GitHubAuth(username, password, store, manifest)
                context, page = await auth.open(browser)
                context.set_default_timeout(20_000)

                with manifest.stage("open_repo"):
                    url = f"https://github.com/{inputs.base_repo}"
                    response = await page.goto(url, wait_until="domcontentloaded")
                    if response is None or response.status >= 400:
                        code = response.status if response else "no response"
                        raise RuntimeError(f"Could not open {url} (HTTP {code})")
                    manifest.page_visited(page.url, await page.title(), "open_repo")
            finally:
                await browser.close()
        status = "success"
    except Exception as e:
        error = REDACTOR.scrub(f"{type(e).__name__}: {e}")
        log.error("Run failed: %s", error)
    finally:
        manifest.finish(status, error)
        path = manifest.write()

    s = manifest.data["session"]
    how = "reused saved session" if s["reused"] else f"full login ({s['dead_session_reason']})"
    log.info("Run %s in %.1fs - %s - manifest: %s",
             status.upper(), manifest.data["total_seconds"], how, path.relative_to(ROOT))
    return 0 if status == "success" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
