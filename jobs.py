"""Job queue in front of run.py.

    python jobs.py add inputs/example.json [inputs/example2.json ...] [--break-step STEP]
    python jobs.py list
    python jobs.py work [--parallel N] [--once] [--watch] [--visible]

`add` checks each input file and puts it in the queue. `work` runs the queued
jobs through run.py (headless unless --visible), one after another or up to N
at the same time with --parallel N, and stops
when the queue is empty; with --watch it keeps waiting for new jobs (Ctrl+C
to stop). `list` shows every job and its result. Jobs live in jobs/.
"""
import argparse
import sys

from bot import faults
from bot.config import ROOT
from bot.inputs import InputError
from bot.jobs import MAX_PARALLEL, JobQueue, WorkerBusy, parallel_count, work_loop

QUEUE_DIR = ROOT / "jobs"


def cmd_add(queue: JobQueue, args) -> int:
    code = 0
    for input_file in args.inputs:
        try:
            job = queue.add(input_file, break_step=args.break_step)
        except InputError as e:
            print(f"NOT added {input_file}: {e}")
            code = 2
            continue
        print(f"added  {job.id}")
    return code


def cmd_list(queue: JobQueue, args) -> int:
    jobs = queue.list()
    if not jobs:
        print("The queue is empty.")
        return 0
    print(f"{'status':8} {'job id':48} {'result'}")
    for j in jobs:
        result = j["output_dir"] or ""
        if j["status"] == "failed":
            result = f"{result}  {j['error'] or ''}".strip()
        print(f"{j['status']:8} {j['id']:48} {result}")
    return 0


def cmd_work(queue: JobQueue, args) -> int:
    try:
        queue.acquire_worker_lock()
    except WorkerBusy as e:
        print(f"Not started: {e}")
        return 1
    try:
        done, failed = work_loop(queue, ROOT / "output", headless=not args.visible,
                                 once=args.once, watch=args.watch, parallel=args.parallel,
                                 say=lambda line: print(line, flush=True))
        print(f"Worker finished: {done} done, {failed} failed.")
        return 0 if failed == 0 else 1
    except KeyboardInterrupt:
        print("Stopped. A job that was running stays in jobs/running/ and runs again next time.")
        return 130
    finally:
        queue.release_worker_lock()


def main() -> int:
    p = argparse.ArgumentParser(description="Job queue for run.py")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("add", help="put input files in the queue")
    a.add_argument("inputs", nargs="+", help="input JSON file(s)")
    a.add_argument("--break-step", choices=faults.STEPS, help="test mode: make this step fail")
    sub.add_parser("list", help="show all jobs")
    w = sub.add_parser("work", help="run queued jobs")
    w.add_argument("--once", action="store_true", help="run at most one job (one per slot with --parallel)")
    w.add_argument("--parallel", type=parallel_count, default=1, metavar="N",
                   help=f"run up to N jobs at the same time (1-{MAX_PARALLEL}, default 1)")
    w.add_argument("--watch", action="store_true", help="keep waiting for new jobs")
    w.add_argument("--visible", action="store_true", help="show the browser window")
    args = p.parse_args()

    queue = JobQueue(QUEUE_DIR)
    return {"add": cmd_add, "list": cmd_list, "work": cmd_work}[args.command](queue, args)


if __name__ == "__main__":
    sys.exit(main())
