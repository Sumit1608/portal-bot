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
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit, urlunsplit

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

log = logging.getLogger("bot.documents")

LOADED = re.compile(r"Showing\s+\d+\s+changed\s+files?|\d+\s+files?\s+changed", re.I)
NOTHING = re.compile(r"There isn.t anything to compare", re.I)
PDF_DATE = re.compile(rb"/(CreationDate|ModDate)\s*\(D:(\d{4,14})")

FIX_TIMES_JS = """() => {
  document.querySelectorAll('relative-time, time-ago, local-time, time').forEach(el => {
    const when = el.getAttribute('datetime');
    const span = document.createElement('span');
    span.textContent = when ? when.slice(0, 10) : el.textContent;
    el.replaceWith(span);
  });
}"""


class DocumentError(RuntimeError):
    """The portal did not produce a usable document. Can be retried."""


def compare_url(inputs) -> str:
    """The comparison address built from the inputs (used if the form's own address can't be used)."""
    base = quote(inputs.base_branch, safe="/")
    head = quote(inputs.head_branch, safe="/")
    if inputs.head_repo.lower() != inputs.base_repo.lower():
        owner, name = inputs.head_repo.split("/", 1)
        head = f"{owner}:{name}:{head}"
    return f"https://github.com/{inputs.base_repo}/compare/{base}...{head}"


def without_query(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def normalize_pdf(path: Path) -> None:
    """Zero the creation/modification dates (same length, so the PDF stays valid)."""
    data = path.read_bytes()
    fixed = PDF_DATE.sub(lambda m: m.group(0)[: -len(m.group(2))] + b"0" * len(m.group(2)), data)
    path.write_bytes(fixed)


async def wait_for_comparison(page: Page, timeout: float = 30) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for pattern, result in ((LOADED, "diff"), (NOTHING, "nothing")):
            matches = page.get_by_text(pattern)
            for i in range(min(await matches.count(), 5)):
                if await matches.nth(i).is_visible():
                    return result
        await page.wait_for_timeout(300)
    raise DocumentError("The comparison did not finish loading")


async def _settle(page: Page) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass        # some pages keep a connection open; the content is already there


async def capture_documents(playwright: Playwright, browser: Browser, context: BrowserContext,
                            page: Page, inputs, manifest, out_dir: Path, headless: bool) -> None:
    docs = out_dir / "documents"
    docs.mkdir(parents=True, exist_ok=True)
    source = without_query(page.url) if "/compare/" in page.url else compare_url(inputs)

    with manifest.stage("capture_document"):
        state = await context.storage_state()      # the signed-in session, for the printer

        # ---- 1. print to PDF -------------------------------------------------
        printer_browser = browser if headless else await playwright.chromium.launch(headless=True)
        try:
            printer = await printer_browser.new_context(storage_state=state,
                                                        viewport={"width": 1280, "height": 900})
            pdf_page = await printer.new_page()
            await pdf_page.goto(source, wait_until="domcontentloaded")
            outcome = await wait_for_comparison(pdf_page)
            if outcome == "nothing":
                log.warning("Document: GitHub says there is nothing to compare for these branches")
            await _settle(pdf_page)
            await pdf_page.evaluate(FIX_TIMES_JS)
            await pdf_page.emulate_media(media="screen")
            pdf_path = docs / f"{inputs.label}.pdf"
            await pdf_page.pdf(path=str(pdf_path), format="A4", print_background=True,
                               margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"})
            await printer.close()
        finally:
            if printer_browser is not browser:
                await printer_browser.close()

        normalize_pdf(pdf_path)
        if pdf_path.stat().st_size < 5_000 or not pdf_path.read_bytes().startswith(b"%PDF"):
            raise DocumentError("The printed PDF is empty or invalid")
        manifest.add_file(pdf_path, "comparison printed to PDF")
        manifest.page_visited(source, "comparison (printed)", "capture_document")
        log.info("Document: printed comparison to %s", pdf_path.relative_to(out_dir))

        # ---- 2. the .patch in a new tab --------------------------------------
        tab = await context.new_page()
        try:
            patch_url = source + ".patch"
            response = await tab.goto(patch_url, wait_until="domcontentloaded")
            if response is None or not response.ok:
                patch_url = compare_url(inputs) + ".patch"
                response = await tab.goto(patch_url, wait_until="domcontentloaded")
            if response is None or not response.ok:
                code = response.status if response else "no response"
                raise DocumentError(f"GitHub did not return the patch (HTTP {code})")
            body = await response.body()
            manifest.page_visited(patch_url, "comparison as patch (new tab)", "capture_document")
        finally:
            await tab.close()
            await page.bring_to_front()

        if outcome == "diff" and b"diff --git" not in body:
            raise DocumentError("The patch GitHub returned contains no changes")
        patch_path = docs / f"{inputs.label}.patch"
        patch_path.write_bytes(body)
        manifest.add_file(patch_path, "comparison as git patch (new tab)")
        log.info("Document: saved patch from a new tab to %s", patch_path.relative_to(out_dir))

        manifest.data["document"] = {"source_url": source, "comparison": outcome}
