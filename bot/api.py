"""A small HTTP API in front of the job queue (Python standard library only).

    GET  /health          {"ok": true}                       no token needed
    POST /jobs            body: input JSON (+ optional "break_step") -> 201 and the job
    GET  /jobs            {"jobs": [...]} every job, oldest first
    GET  /jobs/<id>       one job, or 404

Every request except /health must send  Authorization: Bearer <API_TOKEN>.
Answers are JSON. The API only queues and reports: the worker (in the same
process with --worker, or `python jobs.py work`) does the actual runs.
"""
import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .inputs import InputError
from .jobs import JobQueue

MAX_BODY = 64 * 1024                 # an input file is a few hundred bytes
log = logging.getLogger("bot.api")


class ApiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], queue: JobQueue, token: str,
                 worker_running: Callable[[], bool] = lambda: False):
        if not token:
            raise ValueError("an API token is required")
        self.queue = queue
        self.token = token
        self.worker_running = worker_running
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: ApiServer
    server_version = "portal-bot-api"
    sys_version = ""

    # ------------------------------------------------------------ routes
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/health":
            return self._send(200, {"ok": True})
        if not self._authorized():
            return
        q = self.server.queue
        if path == "/jobs":
            return self._send(200, {"jobs": q.list(), "counts": q.counts(),
                                    "worker_running": self.server.worker_running()})
        if path.startswith("/jobs/"):
            job = q.get(path[len("/jobs/"):])
            return self._send(200, job) if job else self._send(404, {"error": "no such job"})
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path != "/jobs":
            return self._send(404, {"error": "not found"})
        if not self._authorized():
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0:
            return self._send(400, {"error": "send the input JSON as the request body"})
        if length > MAX_BODY:
            return self._send(413, {"error": f"body larger than {MAX_BODY} bytes"})
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            return self._send(400, {"error": f"body is not valid JSON: {e}"})
        if not isinstance(body, dict):
            return self._send(400, {"error": "body must be a JSON object { ... }"})
        break_step = body.pop("break_step", None)
        try:
            job = self.server.queue.add_data(body, break_step=break_step)
        except InputError as e:
            return self._send(400, {"error": str(e)})
        log.info("API: queued job %s", job.id)
        self._send(201, job.data, location=f"/jobs/{job.id}")

    def do_PUT(self) -> None:
        self._send(405, {"error": "method not allowed"})

    do_DELETE = do_PATCH = do_PUT

    # ------------------------------------------------------------ helpers
    def _authorized(self) -> bool:
        given = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.token}"
        if hmac.compare_digest(given.encode(), expected.encode()):   # constant-time compare
            return True
        self._send(401, {"error": "missing or wrong token (Authorization: Bearer <API_TOKEN>)"},
                   extra={"WWW-Authenticate": "Bearer"})
        return False

    def _send(self, status: int, payload: dict, location: str | None = None,
              extra: dict | None = None) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if location:
            self.send_header("Location", location)
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        # method, path and status only; headers (the token) are never logged
        log.info("API: %s %s -> %s", self.command, self.path.split("?", 1)[0], args[1] if len(args) > 1 else "")
