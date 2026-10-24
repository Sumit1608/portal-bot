"""A small job queue in front of run.py, kept in folders on disk.

    jobs/pending/<id>.json    waiting to run (oldest first)
    jobs/running/<id>.json    taken by the worker
    jobs/done/<id>.json       run.py exited 0
    jobs/failed/<id>.json     run.py exited non-zero (or was cut off)
    jobs/inputs/<id>.json     frozen copy of the job's input file
    jobs/logs/<id>.log        console output of that run (already redacted by run.py)

A job moves between folders by renaming its file, which is atomic, so a job
is never in two states at once. Only one worker may run at a time (the saved
GitHub session is shared): the worker holds an OS lock on jobs/worker.lock,
which the OS releases if the worker crashes. On start, a worker puts jobs
left in running/ by a crashed worker back into pending/.
"""
import json
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .inputs import load_inputs

STATES = ("pending", "running", "done", "failed")
JOB_TIMEOUT = 15 * 60            # seconds; a run normally takes under a minute


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class WorkerBusy(RuntimeError):
    """Another worker already holds the queue."""


@dataclass
class Job:
    path: Path
    data: dict

    @property
    def id(self) -> str:
        return self.data["id"]


class JobQueue:
    def __init__(self, root: Path):
        self.root = root
        for name in (*STATES, "inputs", "logs"):
            (root / name).mkdir(parents=True, exist_ok=True)
        self._lock_file = None

    # ------------------------------------------------------------ adding
    def add(self, input_file: str | Path, break_step: str | None = None) -> Job:
        """Validate the input file now (bad input never enters the queue)."""
        inputs = load_inputs(input_file)                 # raises InputError
        job_id = f"{datetime.now():%Y%m%d-%H%M%S-%f}-{inputs.label}-{secrets.token_hex(2)}"
        frozen = self.root / "inputs" / f"{job_id}.json"
        frozen.write_text(Path(input_file).read_text(encoding="utf-8-sig"), encoding="utf-8")
        data = {
            "id": job_id,
            "status": "pending",
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "input_file": str(input_file),
            "inputs": inputs.to_dict(),
            "break_step": break_step,
            "exit_code": None,
            "run_status": None,
            "error": None,
            "output_dir": None,
        }
        path = self.root / "pending" / f"{job_id}.json"
        self._write(path, data)
        return Job(path, data)

    # ------------------------------------------------------------ worker side
    def acquire_worker_lock(self) -> None:
        fh = open(self.root / "worker.lock", "a+")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise WorkerBusy("another worker is already running on this queue") from None
        self._lock_file = fh

    def release_worker_lock(self) -> None:
        if self._lock_file:
            self._lock_file.close()          # closing the file releases the lock
            self._lock_file = None

    def recover(self) -> list[str]:
        """Put jobs a crashed worker left in running/ back at the front of the queue."""
        recovered = []
        for path in sorted((self.root / "running").glob("*.json")):
            job = Job(path, json.loads(path.read_text(encoding="utf-8")))
            job.data.update(status="pending", started_at=None)
            self._move(job, "pending")
            recovered.append(job.id)
        return recovered

    def claim(self) -> Job | None:
        """Take the oldest pending job, or None if the queue is empty."""
        for path in sorted((self.root / "pending").glob("*.json")):
            target = self.root / "running" / path.name
            try:
                path.replace(target)
            except FileNotFoundError:
                continue                           # removed meanwhile
            job = Job(target, json.loads(target.read_text(encoding="utf-8")))
            job.data.update(status="running", started_at=_now())
            self._write(target, job.data)
            return job
        return None

    def finish(self, job: Job, exit_code: int, manifest: dict | None, error: str | None = None) -> None:
        job.data.update(
            status="done" if exit_code == 0 else "failed",
            finished_at=_now(),
            exit_code=exit_code,
            run_status=manifest.get("status") if manifest else None,
            error=error or (manifest.get("error") if manifest else None),
        )
        self._move(job, job.data["status"])

    # ------------------------------------------------------------ reading
    def list(self) -> list[dict]:
        jobs = []
        for state in STATES:
            for path in (self.root / state).glob("*.json"):
                jobs.append(json.loads(path.read_text(encoding="utf-8")))
        return sorted(jobs, key=lambda j: j["id"])

    def input_path(self, job: Job) -> Path:
        return self.root / "inputs" / f"{job.id}.json"

    def log_path(self, job: Job) -> Path:
        return self.root / "logs" / f"{job.id}.log"

    # ------------------------------------------------------------ helpers
    def _move(self, job: Job, state: str) -> None:
        target = self.root / state / job.path.name
        self._write(job.path, job.data)
        job.path.replace(target)
        job.path = target

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)


# ---------------------------------------------------------------- running
Runner = Callable[[list[str], Path], int]


def subprocess_runner(args: list[str], log_file: Path) -> int:
    """Run `python run.py ...` as its own process, console output to log_file."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with open(log_file, "w", encoding="utf-8") as log:
        proc = subprocess.run([sys.executable, *args], cwd=Path(__file__).resolve().parent.parent,
                              stdout=log, stderr=subprocess.STDOUT, env=env, timeout=JOB_TIMEOUT)
    return proc.returncode


def run_job(queue: JobQueue, job: Job, output_root: Path, headless: bool = True,
            runner: Runner = subprocess_runner) -> None:
    """Run one claimed job through run.py and file the result."""
    run_id = f"job-{job.id}"[:80]
    args = ["run.py", "--input", str(queue.input_path(job)), "--run-id", run_id]
    if headless:
        args.append("--headless")
    if job.data.get("break_step"):
        args += ["--break-step", job.data["break_step"]]
    job.data["output_dir"] = str(Path("output") / run_id)

    error = None
    try:
        exit_code = runner(args, queue.log_path(job))
    except subprocess.TimeoutExpired:
        exit_code, error = 1, f"run took longer than {JOB_TIMEOUT // 60} minutes and was stopped"

    manifest_path = output_root / run_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    if exit_code == 2 and not manifest and not error:
        error = "run.py refused to start (bad input or missing .env values), see the job log"
    queue.finish(job, exit_code, manifest, error)
