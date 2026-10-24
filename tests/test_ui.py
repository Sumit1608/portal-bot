"""Offline tests for the small web UI: served safely, and works in a real browser."""
import asyncio
import threading
import urllib.request

import pytest
from playwright.async_api import async_playwright

from bot.api import ApiServer
from bot.jobs import JobQueue

TOKEN = "test-token-0123456789"


@pytest.fixture
def server(tmp_path):
    queue = JobQueue(tmp_path / "jobs")
    srv = ApiServer(("127.0.0.1", 0), queue, TOKEN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", queue
    srv.shutdown()
    srv.server_close()


@pytest.mark.parametrize("path, kind", [("/", "text/html"), ("/ui", "text/html"),
                                        ("/ui.js", "text/javascript"), ("/ui.css", "text/css")])
def test_ui_files_are_served_with_strict_headers(server, path, kind):
    base, _ = server
    with urllib.request.urlopen(base + path, timeout=5) as resp:   # no token needed for the page
        assert resp.headers["Content-Type"].startswith(kind)
        csp = resp.headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert TOKEN not in resp.read().decode()


def test_favicon_is_quietly_empty(server):
    base, _ = server
    with urllib.request.urlopen(base + "/favicon.ico", timeout=5) as resp:
        assert resp.status == 204


def test_ui_queues_a_job_and_lists_it(server):
    base, queue = server

    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(base + "/")

            await page.fill("#token", "wrong-token-0123456789")
            await page.click("#token-form button[type=submit]")
            await page.wait_for_function("document.getElementById('status').textContent === 'Wrong token'")

            await page.fill("#token", TOKEN)
            await page.click("#token-form button[type=submit]")
            await page.wait_for_function("document.getElementById('status').textContent.startsWith('Connected')")

            await page.fill("input[name=base_repo]", "not a repo")       # server-side validation shows
            await page.fill("input[name=base_branch]", "main")
            await page.fill("input[name=head_branch]", "feature")
            await page.click("#job-form button[type=submit]")
            await page.wait_for_function("document.getElementById('job-message').textContent.includes('owner/repository')")

            await page.fill("input[name=base_repo]", "owner/repo")
            await page.fill("input[name=label]", "<img src=x onerror=alert(1)>")   # shown as text only
            await page.click("#job-form button[type=submit]")
            await page.wait_for_function("document.getElementById('job-message').textContent.startsWith('Queued job')")
            await page.wait_for_function("document.querySelectorAll('#jobs tr .pill.pending').length === 1")
            images = await page.locator("#jobs img").count()
            await browser.close()
            return errors, images

    errors, images = asyncio.run(scenario())
    [job] = queue.list()
    assert job["inputs"]["base_repo"] == "owner/repo" and job["status"] == "pending"
    assert images == 0                    # the label was not turned into HTML
    expected = ("status of 401", "status of 400")         # the deliberate wrong token and bad input
    assert [e for e in errors if not any(x in e for x in expected)] == []   # no script errors
