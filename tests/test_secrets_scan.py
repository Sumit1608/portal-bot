"""Offline tests for the sensitive-data scanner used on evidence bundles."""
import base64

from bot.secrets_scan import scan_path, scan_text

SECRETS = {"username": "OctoCat42", "password": "hunter2-pass"}


def test_plain_and_case_insensitive():
    assert scan_text("signed in as octocat42", SECRETS) == ["username"]


def test_url_encoded():
    assert "password" in scan_text("next=hunter2-pass%2F", SECRETS)


def test_base64_hidden_value():
    hidden = base64.b64encode(b"OctoCat42/portal-bot").decode()
    assert scan_text(f'<x name-with-owner="{hidden}">', SECRETS) == ["username"]


def test_clean_text():
    assert scan_text("nothing to see here", SECRETS) == []


def test_images_are_skipped(tmp_path):
    (tmp_path / "shot.png").write_bytes(b"OctoCat42")
    (tmp_path / "page.html").write_text("hello octocat42", encoding="utf-8")
    found = scan_path(tmp_path, SECRETS)
    assert [(f.name, label) for f, label in found] == [("page.html", "username")]
