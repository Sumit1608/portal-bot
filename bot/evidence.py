"""The evidence bundle, written when a run fails for good.

output/<run>/evidence/
  screenshot.png   the page at the moment of failure (identifiers replaced, fields emptied, images hidden)
  page.html        the page's HTML, cleaned like the fixtures (no scripts, tokens, values)
  url.txt          the page address (query string removed, identifiers masked)
  console.log      browser console messages and page errors from the whole run
  evidence.json    stage key, error, whether it was retryable, every retry,
                   and the resolver's attempts (strategy, query, matches)

Everything is redacted (password, 2FA secret and codes, username, REDACT_EXTRA)
and the folder is then scanned; any text file that still contains a sensitive
value is replaced by a notice rather than left on disk.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .fixtures import SANITIZE_JS
from .redact import NEUTRAL_NAME, clean
from .resolver import ResolveError
from .secrets_scan import scan_path
from .util import utc_now, without_query

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

    from .manifest import Manifest
    from .resolver import Resolver
    from .retry import FinalFailure

log = logging.getLogger("bot.evidence")
MAX_CONSOLE_LINES = 500

MASK_PAGE_JS = r"""([idents, neutral]) => {
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const list = idents.filter(s => s && s.length >= 3);
  if (list.length) {
    const find = new RegExp(list.map(esc).join('|'), 'i');
    const swap = new RegExp(list.map(esc).join('|'), 'gi');
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (find.test(node.nodeValue)) node.nodeValue = node.nodeValue.replace(swap, neutral);
    }
    document.title = document.title.replace(swap, neutral);
  }
  document.querySelectorAll('input, textarea').forEach(el => {
    const t = (el.type || '').toLowerCase();
    if (!['hidden', 'submit', 'button', 'checkbox', 'radio'].includes(t)) el.value = '';
  });
  document.querySelectorAll('img').forEach(img => { img.style.visibility = 'hidden'; });
}"""


class Evidence:
    def __init__(self, out_dir: Path, identifiers: list[str], secrets: dict[str, str]):
        self.dir = out_dir / "evidence"
        self.identifiers = [i for i in identifiers if i]
        self.secrets = secrets
        self.console: list[str] = []
        self.pages: list[Page] = []

    # ---- collecting during the run ------------------------------------------
    def watch(self, context: BrowserContext) -> None:
        """Called for every browser context: keep its console output and pages."""
        context.on("page", self._watch_page)
        for page in context.pages:
            self._watch_page(page)

    def _watch_page(self, page: Page) -> None:
        if page in self.pages:
            return
        self.pages.append(page)
        page.on("console", lambda msg: self._add(f"{msg.type}: {msg.text}"))
        page.on("pageerror", lambda err: self._add(f"pageerror: {err}"))

    def _add(self, line: str) -> None:
        self.console.append(f"{datetime.now():%H:%M:%S} {line}")
        del self.console[:-MAX_CONSOLE_LINES]

    def _last_open_page(self) -> Page | None:
        return next((p for p in reversed(self.pages) if not p.is_closed()), None)

    def _write(self, name: str, text: str) -> None:
        (self.dir / name).write_text(clean(text, self.identifiers), encoding="utf-8")

    # ---- writing on final failure -----------------------------------------
    async def _capture_page(self, page: Page) -> list[str]:
        """Screenshot and cleaned HTML; returns what could not be saved."""
        problems = []
        try:
            for frame in page.frames:
                try:
                    await frame.evaluate(MASK_PAGE_JS, [self.identifiers, NEUTRAL_NAME])
                except Exception:
                    pass                    # a frame that went away cannot leak anything
            await page.screenshot(path=str(self.dir / "screenshot.png"), full_page=True,
                                  mask=[page.locator("input[type='password']")])
        except Exception as e:
            problems.append(f"screenshot not taken: {type(e).__name__}")
        try:
            self._write("page.html", await page.evaluate(SANITIZE_JS))
        except Exception as e:
            problems.append(f"page HTML not saved: {type(e).__name__}")
        return problems

    async def write(self, failure: FinalFailure, manifest: Manifest, resolver: Resolver) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        page = self._last_open_page()
        problems = await self._capture_page(page) if page else []
        url = without_query(page.url) if page else "(no open page)"
        self._write("url.txt", url + "\n")
        self._write("console.log", ("\n".join(self.console) or "(no console messages)") + "\n")

        failed_stages = [s["key"] for s in manifest.data["stages"] if s["status"] == "failed"]
        cause = failure.cause
        failing_key = cause.key if isinstance(cause, ResolveError) else None
        report = {
            "captured_at": utc_now(),
            "stage_key": failed_stages[-1] if failed_stages else failure.step,
            "step": failure.step,
            "error_type": type(cause).__name__,
            "error": str(cause),
            "retryable": failure.retryable,
            "attempts_made": failure.attempts,
            "outcome": "retries exhausted" if failure.retryable else "final error, not retried",
            "retries": [r for r in manifest.data["retries"] if r["step"] == failure.step],
            "url": url,
            "resolver": {
                "failing_element": failing_key,
                "failing_element_attempts": resolver.attempts.get(failing_key) if failing_key else None,
                "winning_strategies": dict(resolver.winners),
                "all_attempts": resolver.attempts,
            },
            "problems_writing_evidence": problems,
        }
        self._write("evidence.json", json.dumps(report, indent=2))

        # last line of defence: nothing sensitive may remain in a text file
        leaks = scan_path(self.dir, self.secrets)
        for file, label in leaks:
            file.write_text(f"[removed: contained sensitive data ({label})]\n", encoding="utf-8")
            log.warning("Evidence: %s contained sensitive data and was blanked", file.name)

        files = sorted(f.name for f in self.dir.iterdir())
        manifest.data["evidence"] = {"folder": "evidence", "files": files, "stage_key": report["stage_key"],
                                     "secret_scan": "clean" if not leaks else f"{len(leaks)} file(s) blanked"}
        log.info("Evidence: bundle written to %s (%s)", self.dir.name, ", ".join(files))
        return self.dir
