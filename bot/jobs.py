"""A small job queue in front of run.py, kept in folders on disk.

    jobs/pending/<id>.json    waiting to run (oldest first)
    jobs/running/<id>.json    taken by the worker
    jobs/done/<id>.json       run.py exited 0
    jobs/failed/<id>.json     run.py exited non-zero (or was cut off)
    jobs/inputs/<id>.json     frozen copy of the job's input file
    jobs/logs/<id>.log        console output of that run (already redacted by run.py)

A job moves between folders by renaming its file, which is atomic, so a job
is never in two states at once (and two parallel slots never take the same
job). One worker process serves the queue: it holds an OS lock on
jobs/worker.lock, which the OS releases if the worker crashes. That worker can
run up to MAX_PARALLEL jobs at once (parallel=N); the runs share the saved
login safely through the session lock in run.py. On start, a worker puts jobs
left in running/ by a crashed worker back into pending/.
"""
import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import faults
from .config import ROOT
from .filelock import FileLock
from .inputs import InputError, RunInputs, load_inputs, parse_inputs
from .util import retry_busy, utc_now, write_atomic

STATES = ("pending", "running", "done", "failed")
JOB_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
JOB_TIMEOUT = 15 * 60            # seconds; a run normally takes under a minute
MAX_PARALLEL = 4                 # each job is a full browser; more mostly adds memory use


def parallel_count(text: str) -> int:
    """argparse type for --parallel: a whole number from 1 to MAX_PARALLEL."""
    try:
        n = int(text)
    except ValueError:
        n = 0
    if not 1 <= n <= MAX_PARALLEL:
        raise argparse.ArgumentTypeError(f"must be a whole number from 1 to {MAX_PARALLEL}")
    return n


def _read_json(path: Path) -> dict:
    return retry_busy(lambda: json.loads(path.read_text(encoding="utf-8")))


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
        self._worker_lock = FileLock(root / "worker.lock")
        self._claim_lock = threading.Lock()

    # ------------------------------------------------------------ adding
    def add(self, input_file: str | Path, break_step: str | None = None) -> Job:
        """Validate the input file now (bad input never enters the queue)."""
        inputs = load_inputs(input_file)                 # raises InputError
        return self._add(inputs, Path(input_file).read_text(encoding="utf-8-sig"),
                         str(input_file), break_step)

    def add_data(self, data: object, break_step: str | None = None, source: str = "api") -> Job:
        """Same as add(), for input that arrived as JSON (e.g. an API request)."""
        inputs = parse_inputs(data, "request body")    # raises InputError
        return self._add(inputs, json.dumps(data, indent=2), source, break_step)

    def _add(self, inputs: RunInputs, raw: str, source: str, break_step: str | None) -> Job:
        if break_step is not None and break_step not in faults.STEPS:
            raise InputError(f'"break_step" must be one of {", ".join(faults.STEPS)}')
        job_id = f"{datetime.now():%Y%m%d-%H%M%S-%f}-{inputs.label}-{secrets.token_hex(2)}"
        frozen = self.root / "inputs" / f"{job_id}.json"
        frozen.write_text(raw, encoding="utf-8")
        data = {
            "id": job_id,
            "status": "pending",
            "created_at": utc_now(),
            "started_at": None,
            "finished_at": None,
            "input_file": source,
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
        if not self._worker_lock.try_acquire():
            raise WorkerBusy("another worker is already running on this queue")

    def release_worker_lock(self) -> None:
        self._worker_lock.release()

    def recover(self) -> list[str]:
        """Put jobs a crashed worker left in running/ back at the front of the queue."""
        recovered = []
        for path in sorted((self.root / "running").glob("*.json")):
            job = Job(path, _read_json(path))
            job.data.update(status="pending", started_at=None)
            self._move(job, "pending")
            recovered.append(job.id)
        return recovered

    def claim(self) -> Job | None:
        """Take the oldest pending job, or None if the queue is empty."""
        # Parallel slots are threads of one worker process (the worker lock
        # ensures one process). On Windows two threads renaming the same file at
        # once can both "succeed", so slots take their turn here.
        with self._claim_lock:
            for path in sorted((self.root / "pending").glob("*.json")):
                target = self.root / "running" / path.name
                try:
                    path.replace(target)
                except (FileNotFoundError, PermissionError):
                    continue                       # removed meanwhile
                job = Job(target, _read_json(target))
                job.data.update(status="running", started_at=utc_now())
                self._write(target, job.data)
                return job
        return None

    def finish(self, job: Job, exit_code: int, manifest: dict | None, error: str | None = None) -> None:
        job.data.update(
            status="done" if exit_code == 0 else "failed",
            finished_at=utc_now(),
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
                try:
                    jobs.append(_read_json(path))
                except FileNotFoundError:
                    pass                           # moved to its next state meanwhile
        return sorted(jobs, key=lambda j: j["id"])

    def get(self, job_id: str) -> dict | None:
        if not JOB_ID.match(job_id):
            return None
        for _ in range(2):                         # a second look if it moved while we searched
            for state in STATES:
                try:
                    return _read_json(self.root / state / f"{job_id}.json")
                except FileNotFoundError:
                    continue
        return None

    def counts(self) -> dict[str, int]:
        return {state: len(list((self.root / state).glob("*.json"))) for state in STATES}

    def input_path(self, job: Job) -> Path:
        return self.root / "inputs" / f"{job.id}.json"

    def log_path(self, job: Job) -> Path:
        return self.root / "logs" / f"{job.id}.log"

    # ------------------------------------------------------------ helpers
    def _move(self, job: Job, state: str) -> None:
        target = self.root / state / job.path.name
        self._write(job.path, job.data)
        retry_busy(lambda: job.path.replace(target))
        job.path = target

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        write_atomic(path, json.dumps(data, indent=2))


# ---------------------------------------------------------------- running
Runner = Callable[[list[str], Path], int]


def subprocess_runner(args: list[str], log_file: Path) -> int:
    """Run `python run.py ...` as its own process, console output to log_file."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with open(log_file, "w", encoding="utf-8") as log:
        proc = subprocess.run([sys.executable, *args], cwd=ROOT,
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


def work_loop(queue: JobQueue, output_root: Path, headless: bool = True, once: bool = False,
              watch: bool = False, stop: threading.Event | None = None,
              say: Callable[[str], None] = print, poll_seconds: float = 5,
              runner: Runner = subprocess_runner, parallel: int = 1) -> tuple[int, int]:
    """Run queued jobs, up to `parallel` at the same time; returns (done, failed).

    Each of the `parallel` slots takes the oldest pending job, runs it, and
    takes the next. The caller must hold the worker lock. Stops when the queue
    is empty (once=True: after one job per slot), unless watch=True, then it
    waits for new jobs until `stop` is set.
    """
    if not 1 <= parallel <= MAX_PARALLEL:
        raise ValueError(f"parallel must be between 1 and {MAX_PARALLEL}")
    stop = stop or threading.Event()
    for job_id in queue.recover():
        say(f"recovered {job_id} (left running by a stopped worker) - it will run again")
    totals = {"done": 0, "failed": 0}
    count_lock = threading.Lock()
    tag = (lambda n: f"[{n}] ") if parallel > 1 else (lambda n: "")

    def slot(n: int) -> None:
        while not stop.is_set():
            job = queue.claim()                    # atomic: two slots never get the same job
            if job is None:
                if watch and not once:
                    stop.wait(poll_seconds)
                    continue
                return
            say(f"{tag(n)}running {job.id} ...")
            run_job(queue, job, output_root, headless=headless, runner=runner)
            ok = job.data["status"] == "done"
            with count_lock:
                totals["done" if ok else "failed"] += 1
            if ok:
                say(f"{tag(n)}  done    -> {job.data['output_dir']}")
            else:
                say(f"{tag(n)}  FAILED  -> {job.data['error']}  (log: jobs/logs/{job.id}.log)")
            if once:
                return

    if parallel == 1:
        slot(1)
    else:
        threads = [threading.Thread(target=slot, args=(n,), name=f"slot-{n}", daemon=True)
                   for n in range(1, parallel + 1)]
        for t in threads:
            t.start()
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(0.5)                        # short joins keep Ctrl+C working on Windows
    return totals["done"], totals["failed"]
