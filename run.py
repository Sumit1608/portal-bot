"""Main command.

    python run.py --input inputs/example.json [--headless] [--fresh]
                  [--capture-fixtures] [--break-step STEP]

Signs in to GitHub (reusing the saved session when it is still alive,
otherwise a full login with 2FA), clicks through repository -> Pull requests
-> New pull request, fills the 4-field Compare form, saves the comparison as
a PDF (print) and a .patch (new tab), and writes the manifest and a redacted
run.log. Each step is retried with backoff when the failure is retryable; on
a final failure a redacted evidence bundle is written.
"""
import argparse
import asyncio
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from bot import faults
from bot.compare_flow import CompareForm
from bot.documents import capture_documents
from bot.evidence import Evidence
from bot.github_login import GitHubAuth
from bot.inputs import InputError, load_inputs
from bot.log import setup_logging
from bot.manifest import Manifest
from bot.redact import REDACTOR
from bot.retry import FinalFailure, first_line, run_with_retries
from bot.session import SessionStore

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state" / "github.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GitHub portal automation")
    p.add_argument("--input", required=True, help="path to the input JSON file")
    p.add_argument("--headless", action="store_true", help="run without a visible browser window")
    p.add_argument("--fresh", action="store_true", help="delete the saved session first (forces a full login)")
    p.add_argument("--capture-fixtures", action="store_true",
                   help="save cleaned copies of the login, 2FA and compare pages to fixtures/ (implies --fresh)")
    p.add_argument("--break-step", choices=faults.STEPS,
                   help="make this step fail on purpose, to see retries and the evidence bundle")
    p.add_argument("--run-id", help="name of the output folder (default: date-time-label)")
    args = p.parse_args()
    if args.run_id and not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", args.run_id):
        p.error("--run-id may only contain letters, digits, '.', '_' and '-'")
    return args


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
    extras = [v.strip() for v in os.getenv("REDACT_EXTRA", "").split(",") if v.strip()]
    identifiers = [username, *extras]
    for value in (password, os.getenv("GITHUB_TOTP_SECRET"), *identifiers):
        REDACTOR.add(value)                      # never let these reach a log or evidence

    run_id = args.run_id or f"{datetime.now():%Y%m%d-%H%M%S}-{inputs.label}"
    out_dir = ROOT / "output" / run_id
    if args.run_id and out_dir.exists():
        shutil.rmtree(out_dir)                 # a named run replaces its previous output
    manifest = Manifest(run_id, args.input, inputs.to_dict(), out_dir)
    manifest.record_stage("load_inputs", load_seconds)
    log = setup_logging(out_dir / "run.log")
    log.info("Run %s started with %s", run_id, args.input)

    faults.active = args.break_step
    if args.break_step:
        log.warning("Test mode: step '%s' will fail on purpose (--break-step)", args.break_step)

    store = SessionStore(STATE_FILE)
    if args.fresh or args.capture_fixtures:
        store.clear()
    capture = ROOT / "fixtures" if args.capture_fixtures else None
    evidence = Evidence(out_dir, identifiers)

    status, error = "failed", None
    try:
        async with async_playwright() as p:
            with manifest.stage("launch_browser"):
                browser = await p.chromium.launch(headless=args.headless)
            auth = GitHubAuth(username, password, store, manifest,
                              capture_dir=capture, on_context=evidence.watch)
            try:
                context, page = await run_with_retries(
                    "login", lambda attempt: auth.open(browser), manifest)
                context.set_default_timeout(20_000)

                form = CompareForm(page, auth.resolver, manifest,
                                   capture_dir=capture, identifiers=identifiers)

                async def form_step(attempt: int) -> None:
                    await form.navigate(inputs.base_repo)     # a retry starts from the repository again
                    await form.fill(inputs)

                await run_with_retries("form", form_step, manifest)
                await run_with_retries(
                    "capture_document",
                    lambda attempt: capture_documents(p, browser, context, page, inputs, manifest,
                                                      out_dir, args.headless, on_context=evidence.watch),
                    manifest)
            except FinalFailure as failure:
                await evidence.write(failure, manifest, auth.resolver)
                raise
            finally:
                await browser.close()
        status = "success"
    except FinalFailure as failure:
        error = str(failure)
    except Exception as e:                       # e.g. the browser could not start
        error = f"{type(e).__name__}: {first_line(e)}"
    finally:
        manifest.finish(status, REDACTOR.scrub(error) if error else None)
        path = manifest.write()

    s = manifest.data["session"]
    how = ("reused saved session" if s["reused"] else
           f"full login ({s['dead_session_reason']})" if s["full_login"] else "no session")
    if error:
        log.error("Run FAILED in %.1fs - %s - %s", manifest.data["total_seconds"], how, error)
        if manifest.data["evidence"]:
            log.error("Evidence bundle: %s", (out_dir / "evidence").relative_to(ROOT))
        log.info("Manifest: %s", path.relative_to(ROOT))
    else:
        log.info("Run SUCCESS in %.1fs - %s - manifest: %s",
                 manifest.data["total_seconds"], how, path.relative_to(ROOT))
    for f in manifest.data["captured_files"]:
        log.info("  document: %s (%d bytes, sha256 %s...)", f["path"], f["bytes"], f["sha256"][:12])
    return 0 if status == "success" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
