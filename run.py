"""Main command.

    python run.py --input inputs/example.json [--headless] [--fresh]
                  [--capture-fixtures] [--break-step STEP] [--run-id NAME]

Signs in to GitHub (reusing the saved session when it is still alive,
otherwise a full login with 2FA), clicks through repository -> Pull requests
-> New pull request, fills the 4-field Compare form, saves the comparison as
a PDF (print) and a .patch (new tab), and writes the manifest and a redacted
run.log. Each step is retried with backoff when the failure is retryable; on
a final failure a redacted evidence bundle is written.

Exit code: 0 success, 1 run failed, 2 bad input or missing .env values.
"""
import argparse
import asyncio
import logging
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from bot import faults
from bot.compare_flow import CompareForm
from bot.config import ROOT, Settings, load_settings
from bot.documents import capture_documents
from bot.evidence import Evidence
from bot.github_login import GitHubAuth
from bot.inputs import InputError, RunInputs, load_inputs
from bot.log import setup_logging
from bot.manifest import Manifest
from bot.redact import REDACTOR
from bot.retry import FinalFailure, first_line, run_with_retries
from bot.session import SessionStore

STATE_FILE = ROOT / "state" / "github.json"
FIXTURES_DIR = ROOT / "fixtures"
SESSION_LOCK_TIMEOUT = 10 * 60      # seconds to wait while another run signs in
RUN_ID = re.compile(r"[A-Za-z0-9._-]{1,80}")


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
    if args.run_id and not RUN_ID.fullmatch(args.run_id):
        p.error("--run-id may only contain letters, digits, '.', '_' and '-'")
    return args


async def run_steps(args: argparse.Namespace, inputs: RunInputs, settings: Settings,
                    manifest: Manifest, out_dir: Path, log: logging.Logger) -> None:
    """login -> navigate + fill form -> capture documents; raises FinalFailure."""
    store = SessionStore(STATE_FILE)
    capture = FIXTURES_DIR if args.capture_fixtures else None
    evidence = Evidence(out_dir, settings.identifiers, settings.secrets())

    async with async_playwright() as p:
        with manifest.stage("launch_browser"):
            browser = await p.chromium.launch(headless=args.headless)
        auth = GitHubAuth(settings.username, settings.password, settings.totp_secret, store, manifest,
                          capture_dir=capture, on_context=evidence.watch)
        try:
            # parallel runs share the saved login: one at a time checks, refreshes or saves it
            async with store.lock.hold(timeout=SESSION_LOCK_TIMEOUT) as waited:
                manifest.record_stage("wait_session_lock", waited)
                if waited > 1:
                    log.info("Session: waited %.1fs for another run to finish signing in", waited)
                if args.fresh or args.capture_fixtures:
                    store.clear()
                context, page = await run_with_retries("login", lambda _: auth.open(browser), manifest)
            context.set_default_timeout(20_000)

            form = CompareForm(page, auth.resolver, manifest, capture_dir=capture,
                               identifiers=settings.identifiers)

            async def form_step(_: int) -> None:
                await form.navigate(inputs.base_repo)     # a retry starts from the repository again
                await form.fill(inputs)

            await run_with_retries("form", form_step, manifest)
            await run_with_retries(
                "capture_document",
                lambda _: capture_documents(browser, context, page, inputs, manifest, out_dir,
                                            args.headless, on_context=evidence.watch),
                manifest)
        except FinalFailure as failure:
            await evidence.write(failure, manifest, auth.resolver)
            raise
        finally:
            await browser.close()


def report(manifest: Manifest, manifest_path: Path, out_dir: Path, log: logging.Logger) -> None:
    data, s = manifest.data, manifest.data["session"]
    how = ("reused saved session" if s["reused"] else
           f"full login ({s['dead_session_reason']})" if s["full_login"] else "no session")
    if data["error"]:
        log.error("Run FAILED in %.1fs - %s - %s", data["total_seconds"], how, data["error"])
        if data["evidence"]:
            log.error("Evidence bundle: %s", (out_dir / "evidence").relative_to(ROOT))
        log.info("Manifest: %s", manifest_path.relative_to(ROOT))
    else:
        log.info("Run SUCCESS in %.1fs - %s - manifest: %s",
                 data["total_seconds"], how, manifest_path.relative_to(ROOT))
    for f in data["captured_files"]:
        log.info("  document: %s (%d bytes, sha256 %s...)", f["path"], f["bytes"], f["sha256"][:12])


async def main() -> int:
    args = parse_args()

    started = time.perf_counter()
    try:
        inputs = load_inputs(args.input)
    except InputError as e:
        print(f"Input error: {e}")
        return 2
    load_seconds = time.perf_counter() - started

    settings = load_settings()
    if not settings.has_credentials:
        print("Missing GITHUB_USERNAME or GITHUB_PASSWORD in .env")
        return 2
    settings.register_with_redactor()       # never let these reach a log or evidence

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

    status, error = "failed", None
    try:
        await run_steps(args, inputs, settings, manifest, out_dir, log)
        status = "success"
    except FinalFailure as failure:
        error = str(failure)
    except Exception as e:                       # e.g. the browser could not start
        error = f"{type(e).__name__}: {first_line(e)}"
    finally:
        manifest.finish(status, REDACTOR.scrub(error) if error else None)
        manifest_path = manifest.write()

    report(manifest, manifest_path, out_dir, log)
    return 0 if status == "success" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
