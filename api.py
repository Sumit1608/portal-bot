"""HTTP API in front of the job queue.

    python api.py [--worker] [--parallel N] [--visible] [--port 8000] [--host 127.0.0.1]

Needs API_TOKEN in .env. Listens on this computer only (127.0.0.1) unless
--host says otherwise. With --worker it also runs the queued jobs, so one
command gives a working service; without it, run `python jobs.py work --watch`
next to it. Ctrl+C stops it. Endpoints: see bot/api.py or the README.
"""
import argparse
import os
import sys
import threading

from dotenv import load_dotenv

from bot.api import ApiServer
from bot.config import ROOT
from bot.jobs import JobQueue, WorkerBusy, parallel_count, work_loop
from bot.log import setup_logging
from bot.redact import REDACTOR

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main() -> int:
    p = argparse.ArgumentParser(description="HTTP API for the job queue")
    p.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this computer only)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--worker", action="store_true", help="also run the queued jobs in this process")
    p.add_argument("--visible", action="store_true", help="show the browser window for jobs (with --worker)")
    p.add_argument("--parallel", type=parallel_count, default=1, metavar="N",
                   help="with --worker: run up to N jobs at the same time")
    args = p.parse_args()

    load_dotenv(ROOT / ".env")
    token = os.getenv("API_TOKEN", "").strip()
    if len(token) < 16:
        print("API_TOKEN in .env is missing or shorter than 16 characters.")
        print('Create one with:  python -c "import secrets; print(secrets.token_urlsafe(24))"')
        print("and add a line  API_TOKEN=<that value>  to .env")
        return 2
    REDACTOR.add(token)
    log = setup_logging(ROOT / "output" / "api.log")

    queue = JobQueue(ROOT / "jobs")
    stop = threading.Event()
    worker = None
    if args.worker:
        try:
            queue.acquire_worker_lock()
        except WorkerBusy:
            log.warning("Another worker is already running - this API will only queue jobs")
        else:
            def work() -> None:
                try:
                    work_loop(queue, ROOT / "output", headless=not args.visible, watch=True,
                              stop=stop, say=log.info, parallel=args.parallel)
                finally:
                    queue.release_worker_lock()
            worker = threading.Thread(target=work, name="worker", daemon=True)

    try:
        server = ApiServer((args.host, args.port), queue, token,
                           worker_running=lambda: bool(worker and worker.is_alive()))
    except OSError as e:
        print(f"Cannot listen on {args.host}:{args.port}: {e}")
        return 1
    if args.host not in LOCAL_HOSTS:
        log.warning("Listening on %s: other computers can reach this API. Keep the token secret.", args.host)
    if worker:
        worker.start()
    log.info("API listening on http://%s:%d  (worker: %s)  - Ctrl+C to stop",
             args.host, args.port, f"on, {args.parallel} at a time" if worker else "off")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopping ...")
    finally:
        stop.set()
        server.server_close()
        if worker:
            worker.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
