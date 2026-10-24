"""A small HTTP API in front of the job queue (Python standard library only).

    GET  /                the small web UI (bot/ui/)          no token needed (holds no data)
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
from pathlib import Path
from typing import Callable

from .inputs import InputError
from .jobs import JobQueue

MAX_BODY = 64 * 1024                 # an input file is a few hundred bytes
MAX_DISCARD = 1024 * 1024            # an oversized body is read and dropped up to this much
log = logging.getLogger("bot.api")

# The small web UI (bot/ui/): open http://127.0.0.1:8000/ in a browser.
UI_DIR = Path(__file__).resolve().parent / "ui"
UI_FILES = {                                     # fixed list: no path from the request reaches the disk
    "": ("index.html", "text/html; charset=utf-8"),
    "/ui": ("index.html", "text/html; charset=utf-8"),
    "/ui.js": ("ui.js", "text/javascript; charset=utf-8"),
    "/ui.css": ("ui.css", "text/css; charset=utf-8"),
}
# only this API's own files and calls; no inline code, no other sites, no framing
UI_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                               "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


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
        path = self._route()
        if path == "/health":
            return self._send(200, {"ok": True})
        if path in UI_FILES:                     # the page holds no data; its API calls need the token
            return self._send_file(*UI_FILES[path])
        if path == "/favicon.ico":               # browsers ask for it on their own: no icon, no 401 noise
            return self._respond(204, b"", "image/x-icon")
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
        # Take in the whole body BEFORE any answer (even 401/404/413): closing a
        # connection with unread data makes Windows abort it, and the client
        # would get a network error instead of the answer.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length > MAX_BODY:
            self._discard(min(length, MAX_DISCARD))
            self.close_connection = True
            return self._send(413, {"error": f"body larger than {MAX_BODY} bytes"},
                              extra={"Connection": "close"})
        raw = self.rfile.read(length) if length > 0 else b""

        path = self._route()
        if path != "/jobs":
            return self._send(404, {"error": "not found"})
        if not self._authorized():
            return
        if not raw:
            return self._send(400, {"error": "send the input JSON as the request body"})
        try:
            body = json.loads(raw.decode("utf-8-sig"))
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
        self._send(201, job.data, extra={"Location": f"/jobs/{job.id}"})

    def do_PUT(self) -> None:
        self._send(405, {"error": "method not allowed"})

    do_DELETE = do_PATCH = do_PUT

    # ------------------------------------------------------------ helpers
    def _route(self) -> str:
        return self.path.split("?", 1)[0].rstrip("/")

    def _discard(self, length: int) -> None:
        while length > 0:
            chunk = self.rfile.read(min(length, 65536))
            if not chunk:
                break
            length -= len(chunk)

    def _authorized(self) -> bool:
        given = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.token}"
        if hmac.compare_digest(given.encode(), expected.encode()):   # constant-time compare
            return True
        self._send(401, {"error": "missing or wrong token (Authorization: Bearer <API_TOKEN>)"},
                   extra={"WWW-Authenticate": "Bearer"})
        return False

    def _respond(self, status: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send(self, status: int, payload: dict, extra: dict | None = None) -> None:
        self._respond(status, json.dumps(payload, indent=2).encode("utf-8"),
                      "application/json; charset=utf-8", extra)

    def _send_file(self, name: str, content_type: str) -> None:
        self._respond(200, (UI_DIR / name).read_bytes(), content_type, UI_HEADERS)

    def log_message(self, fmt: str, *args) -> None:
        # method, path and status only; headers (the token) are never logged
        log.info("API: %s %s -> %s", self.command, self._route() or "/", args[1] if len(args) > 1 else "")
