"""Offline tests for the HTTP API: token check, queueing, reading jobs."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from bot.api import MAX_BODY, ApiServer
from bot.jobs import JobQueue

TOKEN = "test-token-0123456789"
INPUT = {"base_repo": "owner/repo", "base_branch": "main", "head_branch": "feature", "label": "f"}


@pytest.fixture
def api(tmp_path):
    queue = JobQueue(tmp_path / "jobs")
    server = ApiServer(("127.0.0.1", 0), queue, TOKEN)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", queue
    server.shutdown()
    server.server_close()


def call(url, method="GET", body=None, token=TOKEN, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_health_needs_no_token(api):
    base, _ = api
    assert call(f"{base}/health", token=None) == (200, {"ok": True})


@pytest.mark.parametrize("token", [None, "wrong-token-0123456789"])
def test_token_is_required(api, token):
    base, queue = api
    assert call(f"{base}/jobs", token=token)[0] == 401
    assert call(f"{base}/jobs", "POST", INPUT, token=token)[0] == 401
    assert queue.list() == []


def test_post_queues_a_job_and_get_reads_it(api):
    base, queue = api
    status, job = call(f"{base}/jobs", "POST", {**INPUT, "break_step": "fill_form"})
    assert status == 201 and job["status"] == "pending" and job["break_step"] == "fill_form"
    assert job["inputs"]["head_repo"] == "owner/repo"            # default filled in
    frozen = json.loads(queue.root.joinpath("inputs", f"{job['id']}.json").read_text())
    assert "break_step" not in frozen                            # only the input fields go to run.py

    assert call(f"{base}/jobs/{job['id']}") == (200, job)
    status, listing = call(f"{base}/jobs")
    assert [j["id"] for j in listing["jobs"]] == [job["id"]]
    assert listing["counts"]["pending"] == 1 and listing["worker_running"] is False


@pytest.mark.parametrize("body, raw", [
    ({**INPUT, "base_repo": "not a repo"}, None),
    ({**INPUT, "break_step": "everything"}, None),
    ([1, 2], None),
    (None, b"{not json"),
])
def test_bad_requests_are_rejected(api, body, raw):
    base, queue = api
    status, answer = call(f"{base}/jobs", "POST", body, raw=raw)
    assert status == 400 and answer["error"]
    assert queue.list() == []


def test_oversized_body_is_rejected(api):
    base, _ = api
    assert call(f"{base}/jobs", "POST", raw=b" " * (MAX_BODY + 1))[0] == 413


@pytest.mark.parametrize("path", ["/jobs/nope", "/jobs/..%2F..%2Fstate%2Fgithub", "/other"])
def test_unknown_paths_are_404(api, path):
    base, _ = api
    assert call(f"{base}{path}")[0] == 404


def test_post_to_unknown_path_is_404(api):
    base, queue = api
    assert call(f"{base}/other", "POST", INPUT)[0] == 404
    assert queue.list() == []


def test_server_refuses_to_start_without_token(tmp_path):
    with pytest.raises(ValueError):
        ApiServer(("127.0.0.1", 0), JobQueue(tmp_path / "jobs"), "")
