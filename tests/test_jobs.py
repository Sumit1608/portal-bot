"""Offline tests for the job queue: order, states, recovery, the worker lock."""
import json
import threading
import time

import pytest

from bot.inputs import InputError
from bot.jobs import MAX_PARALLEL, JobQueue, WorkerBusy, run_job, work_loop

INPUT = {"base_repo": "owner/repo", "base_branch": "main", "head_branch": "feature", "label": "f"}


def input_file(tmp_path, name="in.json", **changes):
    path = tmp_path / name
    path.write_text(json.dumps({**INPUT, **changes}), encoding="utf-8")
    return path


def fake_runner(output_root, exit_code=0, status="success"):
    calls = []

    def runner(args, log_file):
        calls.append(args)
        run_id = args[args.index("--run-id") + 1]
        (output_root / run_id).mkdir(parents=True)
        (output_root / run_id / "manifest.json").write_text(
            json.dumps({"status": status, "error": None if exit_code == 0 else "boom"}))
        log_file.write_text("log")
        return exit_code
    runner.calls = calls
    return runner


def test_jobs_run_oldest_first(tmp_path):
    q = JobQueue(tmp_path / "jobs")
    first = q.add(input_file(tmp_path, "a.json", label="a"))
    second = q.add(input_file(tmp_path, "b.json", label="b"))
    assert q.claim().id == first.id
    assert q.claim().id == second.id
    assert q.claim() is None


def test_bad_input_never_enters_the_queue(tmp_path):
    q = JobQueue(tmp_path / "jobs")
    with pytest.raises(InputError):
        q.add(input_file(tmp_path, base_repo="not a repo"))
    assert q.list() == []


def test_input_with_windows_bom_is_accepted(tmp_path):
    path = tmp_path / "bom.json"
    path.write_text(json.dumps(INPUT), encoding="utf-8-sig")
    q = JobQueue(tmp_path / "jobs")
    assert json.loads(q.input_path(q.add(path)).read_text(encoding="utf-8"))["label"] == "f"


def test_input_is_frozen_when_added(tmp_path):
    q = JobQueue(tmp_path / "jobs")
    path = input_file(tmp_path)
    job = q.add(path)
    path.write_text("changed later", encoding="utf-8")
    assert json.loads(q.input_path(job).read_text())["head_branch"] == "feature"


def test_successful_job_is_done(tmp_path):
    q, out = JobQueue(tmp_path / "jobs"), tmp_path / "output"
    q.add(input_file(tmp_path), break_step="fill_form")
    job = q.claim()
    runner = fake_runner(out)
    run_job(q, job, out, runner=runner)
    args = runner.calls[0]
    assert args[:2] == ["run.py", "--input"] and "--headless" in args
    assert args[-2:] == ["--break-step", "fill_form"]
    [saved] = q.list()
    assert saved["status"] == "done" and saved["exit_code"] == 0 and saved["run_status"] == "success"
    assert (tmp_path / "jobs" / "done" / f"{job.id}.json").exists()


def test_failed_job_keeps_the_error(tmp_path):
    q, out = JobQueue(tmp_path / "jobs"), tmp_path / "output"
    q.add(input_file(tmp_path))
    job = q.claim()
    run_job(q, job, out, runner=fake_runner(out, exit_code=1, status="failed"))
    [saved] = q.list()
    assert saved["status"] == "failed" and saved["error"] == "boom"


def test_work_loop_drains_the_queue(tmp_path):
    q, out = JobQueue(tmp_path / "jobs"), tmp_path / "output"
    for name in ("a", "b"):
        q.add(input_file(tmp_path, f"{name}.json", label=name))
    lines = []
    assert work_loop(q, out, runner=fake_runner(out), say=lines.append) == (2, 0)
    assert q.counts() == {"pending": 0, "running": 0, "done": 2, "failed": 0}
    assert sum(line.startswith("running") for line in lines) == 2


def test_parallel_slots_run_jobs_at_the_same_time(tmp_path):
    q, out = JobQueue(tmp_path / "jobs"), tmp_path / "output"
    for name in "abcde":
        q.add(input_file(tmp_path, f"{name}.json", label=name))
    inner = fake_runner(out)
    lock, running, peak = threading.Lock(), [0], [0]

    def slow_runner(args, log_file):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.2)
        with lock:
            running[0] -= 1
        return inner(args, log_file)

    stop_reading, read_errors = threading.Event(), []

    def api_like_reader():                                     # reads while jobs change state
        while not stop_reading.is_set():
            try:
                q.list()
            except Exception as e:
                read_errors.append(e)

    reader = threading.Thread(target=api_like_reader)
    reader.start()
    try:
        assert work_loop(q, out, runner=slow_runner, say=lambda line: None, parallel=2) == (5, 0)
    finally:
        stop_reading.set()
        reader.join()
    assert read_errors == []
    assert peak[0] == 2                                        # two at once, never more
    run_ids = [a[a.index("--run-id") + 1] for a in inner.calls]
    assert len(run_ids) == len(set(run_ids)) == 5             # every job ran exactly once


@pytest.mark.parametrize("n", [0, MAX_PARALLEL + 1])
def test_parallel_is_bounded(tmp_path, n):
    with pytest.raises(ValueError):
        work_loop(JobQueue(tmp_path / "jobs"), tmp_path / "output", parallel=n)


def test_crashed_worker_jobs_are_recovered(tmp_path):
    q = JobQueue(tmp_path / "jobs")
    job = q.add(input_file(tmp_path))
    q.claim()                                   # worker "crashes" here
    assert q.recover() == [job.id]
    assert q.claim().id == job.id


def test_only_one_worker_at_a_time(tmp_path):
    first, second = JobQueue(tmp_path / "jobs"), JobQueue(tmp_path / "jobs")
    first.acquire_worker_lock()
    with pytest.raises(WorkerBusy):
        second.acquire_worker_lock()
    first.release_worker_lock()
    second.acquire_worker_lock()                # free again once the first stops
    second.release_worker_lock()
