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
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .documents import without_query
from .fixtures import SANITIZE_JS, mask_identifiers
from .redact import REDACTOR
from .resolver import ResolveError
from .secrets_scan import load_secrets, scan_path

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

log = logging.getLogger("bot.evidence")

MASK_PAGE_JS = r"""(idents) => {
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const list = idents.filter(s => s && s.length >= 3);
  if (list.length) {
    const find = new RegExp(list.map(esc).join('|'), 'i');
    const swap = new RegExp(list.map(esc).join('|'), 'gi');
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (find.test(node.nodeValue)) node.nodeValue = node.nodeValue.replace(swap, 'octo-user');
    }
    document.title = document.title.replace(swap, 'octo-user');
  }
  document.querySelectorAll('input, textarea').forEach(el => {
    const t = (el.type || '').toLowerCase();
    if (!['hidden', 'submit', 'button', 'checkbox', 'radio'].includes(t)) el.value = '';
  });
  document.querySelectorAll('img').forEach(img => { img.style.visibility = 'hidden'; });
}"""


class Evidence:
    def __init__(self, out_dir: Path, identifiers: list[str]):
        self.dir = out_dir / "evidence"
        self.identifiers = [i for i in identifiers if i]
        self.console: list[str] = []
        self.pages: list[Page] = []

    # ---- collecting during the run ------------------------------------------
    def watch(self, context: BrowserContext) -> None:
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
        stamp = datetime.now().strftime("%H:%M:%S")
        self.console.append(f"{stamp} {line}")
        del self.console[:-500]            # keep the last 500 lines

    def _last_open_page(self) -> Page | None:
        for page in reversed(self.pages):
            if not page.is_closed():
                return page
        return None

    def _clean(self, text: str) -> str:
        return mask_identifiers(REDACTOR.scrub(text), self.identifiers)

    # ---- writing on final failure -----------------------------------------
    async def write(self, failure, manifest, resolver) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        problems: list[str] = []
        page = self._last_open_page()

        url = without_query(page.url) if page else "(no open page)"
        if page:
            try:
                for frame in page.frames:
                    try:
                        await frame.evaluate(MASK_PAGE_JS, self.identifiers)
                    except Exception:
                        pass
                await page.screenshot(path=str(self.dir / "screenshot.png"), full_page=True,
                                      mask=[page.locator("input[type='password']")])
            except Exception as e:
                problems.append(f"screenshot not taken: {type(e).__name__}")
            try:
                html = await page.evaluate(SANITIZE_JS)
                (self.dir / "page.html").write_text(self._clean(html), encoding="utf-8")
            except Exception as e:
                problems.append(f"page HTML not saved: {type(e).__name__}")

        (self.dir / "url.txt").write_text(self._clean(url) + "\n", encoding="utf-8")
        (self.dir / "console.log").write_text(
            self._clean("\n".join(self.console) or "(no console messages)") + "\n", encoding="utf-8")

        failed_stages = [s["key"] for s in manifest.data["stages"] if s["status"] == "failed"]
        cause = failure.cause
        failing_key = cause.key if isinstance(cause, ResolveError) else None
        report = {
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
        (self.dir / "evidence.json").write_text(self._clean(json.dumps(report, indent=2)),
                                                encoding="utf-8")

        # last line of defence: nothing sensitive may remain in a text file
        leaks = scan_path(self.dir, load_secrets())
        for file, label in leaks:
            file.write_text(f"[removed: contained sensitive data ({label})]\n", encoding="utf-8")
            log.warning("Evidence: %s contained sensitive data and was blanked", file.name)

        files = sorted(f.name for f in self.dir.iterdir())
        manifest.data["evidence"] = {"folder": "evidence", "files": files,
                                     "stage_key": report["stage_key"],
                                     "secret_scan": "clean" if not leaks else f"{len(leaks)} file(s) blanked"}
        log.info("Evidence: bundle written to %s (%s)", self.dir.name, ", ".join(files))
        return self.dir
