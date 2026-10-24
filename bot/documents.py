"""Capture the document GitHub produces for the comparison.

Two files are saved in output/<run>/documents/:

  <label>.pdf    - the comparison page PRINTED TO PDF (portal quirk: print to PDF)
  <label>.patch  - the same comparison as a git patch, opened in a NEW TAB of
                   the signed-in browser (portal quirk: new tab)

Printing to PDF only works in a headless browser, so when the run is visible
the page is printed by a second, invisible browser that is given the same
signed-in session.

Repeatable output: relative times ("2 hours ago") are replaced with fixed dates
before printing, and the PDF's creation/modification timestamps are zeroed, so
the same comparison gives the same document on every run. The .patch is plain
text from GitHub and is byte-for-byte identical for the same branches.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from urllib.parse import quote

from playwright.async_api import Error as PlaywrightError

from . import faults
from .errors import RetryableError
from .resolver import visible_indexes
from .util import without_query

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page

    from .inputs import RunInputs
    from .manifest import Manifest

log = logging.getLogger("bot.documents")

LOADED = re.compile(r"Showing\s+\d+\s+changed\s+files?|\d+\s+files?\s+changed", re.I)
NOTHING = re.compile(r"There isn.t anything to compare", re.I)
PDF_DATE = re.compile(rb"/(CreationDate|ModDate)\s*\(D:(\d{4,14})")
MIN_PDF_BYTES = 5_000

FIX_TIMES_JS = """() => {
  document.querySelectorAll('relative-time, time-ago, local-time, time').forEach(el => {
    const when = el.getAttribute('datetime');
    const span = document.createElement('span');
    span.textContent = when ? when.slice(0, 10) : el.textContent;
    el.replaceWith(span);
  });
}"""


class DocumentError(RetryableError):
    """The portal did not produce a usable document."""


def compare_url(inputs: RunInputs) -> str:
    """The comparison address built from the inputs (used if the form's own address can't be used)."""
    base = quote(inputs.base_branch, safe="/")
    head = quote(inputs.head_branch, safe="/")
    if inputs.head_repo.lower() != inputs.base_repo.lower():
        owner, name = inputs.head_repo.split("/", 1)
        head = f"{owner}:{name}:{head}"
    return f"https://github.com/{inputs.base_repo}/compare/{base}...{head}"


def normalize_pdf(path: Path) -> None:
    """Zero the creation/modification dates (same length, so the PDF stays valid)."""
    data = path.read_bytes()
    path.write_bytes(PDF_DATE.sub(lambda m: m.group(0)[: -len(m.group(2))] + b"0" * len(m.group(2)), data))


async def wait_for_comparison(page: Page, timeout: float = 30) -> str:
    """'diff' when the changed files are shown, 'nothing' when the branches are equal."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for pattern, result in ((LOADED, "diff"), (NOTHING, "nothing")):
            if await visible_indexes(page.get_by_text(pattern), limit=5):
                return result
        await page.wait_for_timeout(300)
    raise DocumentError("The comparison did not finish loading")


async def _print_pdf(browser: Browser, state: dict, source: str, path: Path, headless: bool,
                     on_context: Callable) -> str:
    printer_browser = browser if headless else await browser.browser_type.launch(headless=True)
    try:
        printer = await printer_browser.new_context(storage_state=state, viewport={"width": 1280, "height": 900})
        on_context(printer)
        page = await printer.new_page()
        await page.goto(source, wait_until="domcontentloaded")
        outcome = await wait_for_comparison(page)
        if outcome == "nothing":
            log.warning("Document: GitHub says there is nothing to compare for these branches")
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightError:
            pass        # some pages keep a connection open; the content is already there
        await page.evaluate(FIX_TIMES_JS)
        await page.emulate_media(media="screen")
        await page.pdf(path=str(path), format="A4", print_background=True,
                       margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"})
        await printer.close()
    finally:
        if printer_browser is not browser:
            await printer_browser.close()

    normalize_pdf(path)
    if path.stat().st_size < MIN_PDF_BYTES or not path.read_bytes().startswith(b"%PDF"):
        raise DocumentError("The printed PDF is empty or invalid")
    return outcome


async def _fetch_patch(context: BrowserContext, page: Page, source: str, inputs: RunInputs,
                       manifest: Manifest) -> bytes:
    tab = await context.new_page()
    try:
        for patch_url in (source + ".patch", compare_url(inputs) + ".patch"):
            response = await tab.goto(patch_url, wait_until="domcontentloaded")
            if response is not None and response.ok:
                manifest.page_visited(patch_url, "comparison as patch (new tab)", "capture_document")
                return await response.body()
        code = response.status if response else "no response"
        raise DocumentError(f"GitHub did not return the patch (HTTP {code})")
    finally:
        await tab.close()
        await page.bring_to_front()


async def capture_documents(browser: Browser, context: BrowserContext, page: Page, inputs: RunInputs,
                            manifest: Manifest, out_dir: Path, headless: bool,
                            on_context: Callable = lambda context: None) -> None:
    docs = out_dir / "documents"
    docs.mkdir(parents=True, exist_ok=True)
    source = without_query(page.url) if "/compare/" in page.url else compare_url(inputs)

    with manifest.stage("capture_document"):
        if faults.is_broken("capture_document"):
            raise DocumentError("Injected failure (--break-step capture_document)")

        pdf_path = docs / f"{inputs.label}.pdf"
        state = await context.storage_state()      # the signed-in session, for the printer
        outcome = await _print_pdf(browser, state, source, pdf_path, headless, on_context)
        manifest.add_file(pdf_path, "comparison printed to PDF")
        manifest.page_visited(source, "comparison (printed)", "capture_document")
        log.info("Document: printed comparison to %s", pdf_path.relative_to(out_dir))

        body = await _fetch_patch(context, page, source, inputs, manifest)
        if outcome == "diff" and b"diff --git" not in body:
            raise DocumentError("The patch GitHub returned contains no changes")
        patch_path = docs / f"{inputs.label}.patch"
        patch_path.write_bytes(body)
        manifest.add_file(patch_path, "comparison as git patch (new tab)")
        log.info("Document: saved patch from a new tab to %s", patch_path.relative_to(out_dir))

        manifest.data["document"] = {"source_url": source, "comparison": outcome}
