"""Config, database, events, cancellation and secret redaction."""

from __future__ import annotations

import json
import threading
import time

import pytest

from jarvis.core.cancel import Cancelled, CancelToken
from jarvis.core.config import Config
from jarvis.core.db import Database
from jarvis.core.events import EventBus
from jarvis.core.logging_setup import redact, register_secret


def test_config_defaults_merge_and_persist(home):
    cfg = Config()
    assert cfg.get("voice.prefer_headset") is True
    cfg.set("voice.input_device", "Headset (YS-511)")
    again = Config()
    assert again.get("voice.input_device") == "Headset (YS-511)"
    assert again.get("permissions.read") == "allow"  # defaults still present


def test_config_tolerates_bom_and_recovers_from_corruption(home):
    path = home / "config.json"
    home.mkdir(parents=True, exist_ok=True)
    path.write_text("\ufeff" + json.dumps({"user": {"name": "Ada"}}), encoding="utf-8")
    assert Config().get("user.name") == "Ada"
    path.write_text("{ not json", encoding="utf-8")
    cfg = Config()
    assert cfg.get("user.name") == "Aayush"  # defaults
    assert (home / "config.broken.json").exists()


def test_database_migrations_kv_backup_restore(home, tmp_path):
    db = Database()
    assert db.version >= 3
    assert db.fts is True
    db.kv_set("x", {"a": 1})
    assert db.kv_get("x") == {"a": 1}
    backup = db.backup()
    db.kv_set("x", {"a": 2})
    db.restore(backup)
    assert db.kv_get("x") == {"a": 1}
    assert db.integrity() == "ok"
    db.close()


def test_database_recovers_from_corrupt_file(home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "jarvis.db").write_bytes(b"this is not a sqlite database" * 100)
    db = Database()
    assert db.integrity() == "ok"
    assert list(home.glob("jarvis.corrupt-*.db"))
    db.close()


def test_event_bus_isolates_failing_handlers():
    bus = EventBus()
    got = []
    bus.subscribe("x", lambda e: 1 / 0)
    bus.subscribe("x", lambda e: got.append(e.data["v"]))
    bus.subscribe("*", lambda e: got.append("star"))
    bus.publish("x", v=1)
    assert got == [1, "star"]


def test_cancel_token_pause_resume_cancel():
    tok = CancelToken()
    tok.pause()
    released = threading.Event()

    def worker():
        try:
            tok.check()
        except Cancelled:
            released.set()

    th = threading.Thread(target=worker)
    th.start()
    time.sleep(0.3)
    assert not released.is_set()  # blocked while paused
    tok.cancel("stop")
    th.join(2)
    assert released.is_set()
    child = CancelToken(parent=tok)
    assert child.cancelled


def test_redaction_masks_secrets():
    register_secret("supersecretvalue123")
    text = redact("key=sk-ant-abcdefghijklmnopqrstuvwxyz and supersecretvalue123 and password: hunter2pass")
    assert "sk-ant-abcdefghijkl" not in text
    assert "supersecretvalue123" not in text
    assert "hunter2pass" not in text


@pytest.mark.parametrize("cmd,cat", [
    ("Get-ChildItem C:\\", "execute"),
    ("Remove-Item -Recurse -Force C:\\temp\\x", "destructive"),
    ("rm -rf build", "destructive"),
    ("git push --force origin main", "destructive"),
    ("git reset --hard HEAD~1", "destructive"),
    ("reg add HKCU\\Software\\X /v Y /d 1", "system"),
    ("npm test", "execute"),
    ("format D:", "destructive"),
])
def test_command_classification(cmd, cat):
    from jarvis.core.permissions import classify_command

    assert classify_command(cmd) == cat
