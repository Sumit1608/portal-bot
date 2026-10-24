"""The JSON manifest: a record of one run.

It holds the inputs, every page visited, how long each stage took, every retry,
and every captured file (with size and SHA-256 hash, so two runs can be compared).
"""
import hashlib
import json
import os
import platform
import time
from contextlib import contextmanager
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from .util import utc_now, without_query, write_atomic


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Manifest:
    def __init__(self, run_id: str, input_file: str, inputs: dict, out_dir: Path):
        self.out_dir = out_dir
        self.attempt = 1            # set by the retry wrapper
        self._t0 = time.perf_counter()
        self.data = {
            "run_id": run_id,
            "status": "running",
            "started_at": utc_now(),
            "finished_at": None,
            "total_seconds": None,
            "input_file": str(input_file),
            "inputs": inputs,
            "session": {"reused": None, "full_login": None},
            "pages_visited": [],
            "stages": [],
            "captured_files": [],
            "retries": [],
            "error": None,
            "evidence": None,
            "environment": {"python": platform.python_version(), "os": platform.system()},
        }

    def record_stage(self, key: str, seconds: float, status: str = "ok") -> None:
        """Record a stage that was timed outside the manifest."""
        self.data["stages"].append({"key": key, "status": status, "seconds": round(seconds, 3)})

    @contextmanager
    def stage(self, key: str):
        """Time a block of work:  with manifest.stage("login"): ..."""
        entry = {"key": key, "attempt": self.attempt, "status": "running", "seconds": None}
        self.data["stages"].append(entry)
        start = time.perf_counter()
        try:
            yield entry
            entry["status"] = "ok"
        except BaseException:
            entry["status"] = "failed"
            raise
        finally:
            entry["seconds"] = round(time.perf_counter() - start, 3)

    def page_visited(self, url: str, title: str, stage: str) -> None:
        self.data["pages_visited"].append(
            {"url": without_query(url), "title": title, "stage": stage, "at": utc_now()})

    async def visit(self, page: Page, stage: str) -> None:
        """Record the page the browser is on now."""
        try:
            title = await page.title()
        except PlaywrightError:
            title = ""
        self.page_visited(page.url, title, stage)

    def add_file(self, path: Path, kind: str) -> None:
        self.data["captured_files"].append({
            "path": os.path.relpath(path, self.out_dir),
            "kind": kind,
            "bytes": path.stat().st_size,
            "sha256": sha256_of(path),
        })

    def finish(self, status: str, error: str | None = None) -> None:
        self.data["status"] = status
        self.data["error"] = error
        self.data["finished_at"] = utc_now()
        self.data["total_seconds"] = round(time.perf_counter() - self._t0, 3)

    def write(self) -> Path:
        target = self.out_dir / "manifest.json"
        write_atomic(target, json.dumps(self.data, indent=2))
        return target
