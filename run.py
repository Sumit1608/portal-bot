"""Main command.

    python run.py --input inputs/example.json [--headless]

Step 2 version: loads inputs, opens the repository page, writes the manifest.
Login, the form and document capture are added in later steps.
"""
import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from bot.inputs import InputError, load_inputs
from bot.manifest import Manifest

ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GitHub portal automation")
    p.add_argument("--input", required=True, help="path to the input JSON file")
    p.add_argument("--headless", action="store_true", help="run without a visible browser window")
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

    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{inputs.label}"
    out_dir = ROOT / "output" / run_id
    manifest = Manifest(run_id, args.input, inputs.to_dict(), out_dir)
    manifest.record_stage("load_inputs", load_seconds)

    status, error = "failed", None
    try:
        async with async_playwright() as p:
            with manifest.stage("launch_browser"):
                browser = await p.chromium.launch(headless=args.headless)
                page = await browser.new_page()
            try:
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
        error = f"{type(e).__name__}: {e}"
    finally:
        manifest.finish(status, error)
        path = manifest.write()

    print(f"Run {status.upper()} - manifest: {path.relative_to(ROOT)}")
    if error:
        print(f"Error: {error}")
    return 0 if status == "success" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
