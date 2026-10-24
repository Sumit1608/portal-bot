"""Offline tests for the saved-session checks."""
import json
import time

from bot.session import SessionStore


def write(tmp_path, data):
    path = tmp_path / "github.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return SessionStore(path)


def cookie(expires):
    return {"name": "user_session", "value": "x", "domain": "github.com", "expires": expires}


def test_missing_file(tmp_path):
    assert SessionStore(tmp_path / "none.json").usable_state() == (None, "no saved session")


def test_garbled_file_is_detected_and_kept_aside(tmp_path):
    store = write(tmp_path, "{ not json")
    path, reason = store.usable_state()
    assert path is None and "corrupt" in reason
    assert not store.path.exists()
    assert (tmp_path / "github.json.corrupt").exists()


def test_wrong_shape_is_corrupt(tmp_path):
    assert "corrupt" in write(tmp_path, {"cookies": "nope"}).usable_state()[1]


def test_no_session_cookie(tmp_path):
    store = write(tmp_path, {"cookies": [{"name": "other", "value": "1", "domain": "github.com"}]})
    assert "no GitHub session cookie" in store.usable_state()[1]


def test_expired_cookie(tmp_path):
    store = write(tmp_path, {"cookies": [cookie(time.time() - 60)]})
    assert store.usable_state()[1] == "session cookie has expired"


def test_good_state(tmp_path):
    store = write(tmp_path, {"cookies": [cookie(time.time() + 3600)], "origins": []})
    assert store.usable_state() == (store.path, "saved session found")
