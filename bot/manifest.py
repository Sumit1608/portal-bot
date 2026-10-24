"""The JSON manifest: a record of one run.

It holds the inputs, every page visited, how long each stage took, and every
captured file (with size and SHA-256 hash, so two runs can be compared).
"""
import hashlib
import json
import os
import platform
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_url(url: str) -> str:
    """Drop query string and fragment - they can carry tokens."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


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
            "started_at": _now(),
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
            {"url": _clean_url(url), "title": title, "stage": stage, "at": _now()}
        )

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
        self.data["finished_at"] = _now()
        self.data["total_seconds"] = round(time.perf_counter() - self._t0, 3)

    def write(self) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        target = self.out_dir / "manifest.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(target)   # atomic: never leaves a half-written manifest
        return target
