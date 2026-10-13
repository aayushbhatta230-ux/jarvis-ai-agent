"""Screen sharing routing, file access, email and messaging tools (no real network or accounts)."""

from __future__ import annotations

import threading
import time

import pytest

from tests.v4.fakes import FakeProvider, reply


@pytest.mark.parametrize("text,expected", [
    ("what's on my screen", True), ("can you see my screen", True), ("explain this error", True),
    ("read my screen and then go to instagram and text aayush", False),
    ("can you play the video on my screen", False), ("how are you", False),
])
def test_screen_questions_are_answered_directly(app, text, expected):
    assert app._is_screen_question(text) is expected


def test_share_screen_phrases():
    from jarvis.app import _SHARE_OFF, _SHARE_ON

    assert _SHARE_ON.search("share my screen") and _SHARE_ON.search("start screen sharing")
    assert _SHARE_OFF.search("stop sharing") and _SHARE_OFF.search("stop screen sharing")
    assert not _SHARE_ON.search("what's on my screen")


def test_screen_question_uses_one_call_with_screen_text(app, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(app.screen, "latest_frame", lambda max_age=0: (Image.new("RGB", (64, 64)),
                                                                        "ERROR: ModuleNotFoundError: No module named x"))
    msgs = []
    app.events.subscribe("assistant.message", lambda e: msgs.append(e.data["text"]))
    app.start(voice=False)
    FakeProvider.scripts = {"fake": [lambda m: reply("You're missing the module x - pip install x.")
                                     if "ModuleNotFoundError" in m[0]["content"] else reply("no screen context")]}
    app.submit_input("explain this error on my screen")
    end = time.time() + 10
    while time.time() < end and not msgs:
        time.sleep(0.05)
    assert msgs and "pip install x" in msgs[-1]
    assert len(FakeProvider.calls) == 1  # no agent loop


def test_full_file_access_but_system_folders_protected(app, tmp_path):
    from jarvis.core.permissions import PermissionDenied

    app.config.set("permissions.allowed_roots", ["*"])
    assert app.permissions.check_path(tmp_path / "anywhere.txt", write=True)
    for bad in (r"C:\Windows\System32\x.dll", r"C:\Program Files\x\y.txt", "C:\\"):
        with pytest.raises(PermissionDenied):
            app.permissions.check_path(bad, write=True)


class _FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=0):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, pw):
        self.user = user

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)
        return {}


def test_send_email_requires_approval_and_confirms(app, monkeypatch):
    from jarvis.tools import communication

    monkeypatch.setattr(communication.secrets, "get", lambda n: {"JARVIS_GMAIL_ADDRESS": "me@gmail.com",
                                                                 "JARVIS_GMAIL_APP_PASSWORD": "abcd efgh"}.get(n))
    monkeypatch.setattr(communication.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(communication, "_verify_sent", lambda *a: True)
    _FakeSMTP.sent = []
    app.config.set("permissions.external", "ask")
    seen = []

    def approve(ev):
        seen.append(ev.data["summary"])
        threading.Timer(0.1, lambda: app.permissions.respond(ev.data["id"], True)).start()

    app.events.subscribe("permission.request", approve)
    r = app.tools.execute("send_email", {"to": "friend@example.org", "subject": "Hi", "body": "Sorry about today."})
    assert r.ok, r.error
    assert "Sorry about today." in seen[0]  # the user saw the full message before sending
    assert _FakeSMTP.sent and _FakeSMTP.sent[0]["To"] == "friend@example.org"
    assert "Email sent" in r.output and "Sent folder" in r.output


def test_send_email_denied_sends_nothing(app, monkeypatch):
    from jarvis.tools import communication

    monkeypatch.setattr(communication.smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.sent = []
    app.config.set("permissions.external", "ask")
    app.events.subscribe("permission.request",
                         lambda ev: threading.Timer(0.1, lambda: app.permissions.respond(ev.data["id"], False)).start())
    r = app.tools.execute("send_email", {"to": "a@b.co", "subject": "x", "body": "y"})
    assert not r.ok and not _FakeSMTP.sent


def test_email_needs_known_address(app, monkeypatch):
    from jarvis.tools import communication

    monkeypatch.setattr(communication.secrets, "get", lambda n: "x")
    app.config.set("permissions.external", "allow")
    r = app.tools.execute("send_email", {"to": "Mystery Person", "subject": "x", "body": "y"})
    assert not r.ok and "don't have an email address" in r.error
    app.memory.add("Contact 'Mystery Person': mystery@mail.com", kind="contact")
    from jarvis.tools.registry import ToolContext
    from jarvis.core.cancel import CancelToken

    assert communication.resolve_recipient(ToolContext(app, CancelToken()), "Mystery Person") == "mystery@mail.com"


def test_messaging_tools_are_external_and_routed(app):
    from jarvis.agent.engine import select_groups

    assert app.tools.tools["send_instagram_message"].category == "external"
    assert app.tools.tools["send_email"].category == "external"
    groups = select_groups("read my screen and then go to instagram and text aayush a sorry note")
    assert {"comms", "screen", "desktop"} <= groups
