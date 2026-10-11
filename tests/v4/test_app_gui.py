"""Conversation routing, diagnostics, GUI construction/responsiveness and lifecycle."""

from __future__ import annotations

import threading
import time

import pytest

from jarvis.app import _ACTION, _STOP
from tests.v4.fakes import FakeProvider, reply


@pytest.mark.parametrize("text,is_action", [
    ("open notepad", True), ("Can you fix the failing tests in my repo", True), ("remind me at 5pm", True),
    ("please search for the latest PySide6 release", True), ("how are you today?", False),
    ("tell me a joke", False), ("what is the capital of Nepal", False),
])
def test_action_routing(text, is_action):
    assert bool(_ACTION.match(text)) == is_action


def test_stop_phrases():
    assert _STOP.match("stop") and _STOP.match("never mind.") and not _STOP.match("stop the dev server")


def _wait(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_chat_streams_and_hands_off_to_agent(app):
    msgs = []
    app.events.subscribe("assistant.message", lambda e: msgs.append(e.data["text"]))
    deltas = []
    app.events.subscribe("assistant.delta", lambda e: deltas.append(e.data["text"]))
    app.start(voice=False)
    FakeProvider.scripts = {"fake": [reply("Hello! I'm doing well.")]}
    app.submit_input("how are you?")
    assert _wait(lambda: msgs)
    assert msgs[-1] == "Hello! I'm doing well." and "".join(deltas) == "Hello! I'm doing well."
    # model decides an action is needed -> agent takes over
    started = []
    app.events.subscribe("assistant.task_started", lambda e: started.append(e.data["goal"]))
    FakeProvider.scripts = {"fake": [reply("[[ACTION]]"),
                                     reply(calls=[("task_complete", {"summary": "Checked.", "verified": True})])]}
    app.submit_input("my laptop feels slow, is the memory full?")
    assert _wait(lambda: started) and _wait(lambda: "Checked." in msgs)
    assert not any("[[ACTION]]" in d for d in deltas)


def test_stop_cancels_running_task(app):
    app.start(voice=False)
    FakeProvider.scripts = {"fake": [lambda m: (time.sleep(0.8), reply(calls=[("list_directory", {"path": "."})]))[1]
                                     for _ in range(20)]}
    app.submit_input("open the folder and keep working")
    assert _wait(lambda: bool(app.agent.active()))
    app.submit_input("stop")
    assert _wait(lambda: not app.agent.active(), 10)


def test_permission_answered_by_text(app, workdir):
    app.config.set("permissions.destructive", "ask")
    app.start(voice=False)
    asked = threading.Event()
    app.events.subscribe("permission.request", lambda e: asked.set())
    result = {}
    th = threading.Thread(target=lambda: result.update(r=app.tools.execute("kill_process", {"name": "nonexistent.exe"})))
    th.start()
    assert asked.wait(5)
    app.submit_input("no")
    th.join(5)
    assert not result["r"].ok and "not permitted" in result["r"].error


def test_diagnostics_report(app):
    checks = app.diagnostics.run(quick=True)
    names = {c.name for c in checks}
    assert {"database", "scheduler", "tools", "capture"} <= names
    assert all(c.status in ("ready", "degraded", "unavailable") for c in checks)
    text = app.diagnostics.format(checks)
    assert "ready" in text


def test_hotkey_parsing():
    from jarvis.ui.hotkeys import parse

    assert parse("ctrl+alt+j") == (0x2 | 0x1, ord("J"))
    assert parse("ctrl+shift+space")[1] == 0x20
    with pytest.raises(ValueError):
        parse("ctrl+alt")


def test_gui_builds_reacts_and_stays_responsive(app):
    from PySide6.QtWidgets import QApplication

    from jarvis.ui.main_window import MainWindow

    qt = QApplication.instance() or QApplication([])
    win = MainWindow(app)
    win.show()
    for mode in ("conversation", "activity", "compact"):
        win.set_mode(mode)
        qt.processEvents()
    for state in ("listening", "processing", "speaking", "executing", "analyzing_screen", "coding", "waiting",
                  "recovering", "completed", "paused", "idle"):
        app.events.state(state)
        qt.processEvents()
        assert win.orb.state == state
    # flood of events from a worker thread must not block the UI thread
    def flood():
        for i in range(2000):
            app.events.publish("audio.level", source="mic", level=(i % 10) / 10)
        app.events.publish("assistant.message", text="flood done", final=True)

    t = time.perf_counter()
    th = threading.Thread(target=flood)
    th.start()
    th.join()
    from jarvis.ui.chat import Message

    deadline = time.time() + 10
    seen = False
    while time.time() < deadline and not seen:
        qt.processEvents()
        seen = any(m.raw == "flood done" for m in win.chat.feed.findChildren(Message))
    elapsed = time.perf_counter() - t
    assert seen and elapsed < 5, elapsed  # 2,000 cross-thread events drained without stalling the UI
    app.events.publish("permission.request", id="abc", tool="delete_path", category="destructive",
                       summary="move x to bin")
    qt.processEvents()
    assert "abc" in win._cards
    win.hotkeys.unregister_all()
    win.tray.hide()
    win.close()
