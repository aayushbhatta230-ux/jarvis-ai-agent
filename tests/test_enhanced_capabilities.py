"""Tests for enhanced JARVIS capabilities:
Smart Alarms, Git Intelligence, Web Researcher, Clipboard, and Dev Assistant.
"""

from __future__ import annotations

import time
import pytest

from tools.smart_alarms import SmartAlarmManager
from tools.git_intel import get_git_status, get_recent_commits
from tools.web_researcher import research_topic
from tools.clipboard_intel import set_clipboard_text, get_clipboard_text, inspect_clipboard
from tools.dev_assistant import analyze_code_file


def test_smart_alarms_lifecycle():
    mgr = SmartAlarmManager()
    done_signals = []

    mgr.register_done_hook(lambda item: done_signals.append(item.label))

    # Create a 0.005 min (0.3 second) timer
    t = mgr.set_timer(0.005, "Test Coffee Timer")
    assert t.timer_id > 0
    assert t.label == "Test Coffee Timer"
    assert t.duration_seconds >= 0.3

    active = mgr.list_timers()
    assert len(active) >= 1
    assert any(x["label"] == "Test Coffee Timer" for x in active)

    # Wait for completion
    time.sleep(0.4)
    assert t.completed is True
    assert "Test Coffee Timer" in done_signals

    # Cancellation test
    t2 = mgr.set_timer(10.0, "Long Timer")
    assert mgr.cancel_timer(t2.timer_id) is True
    assert t2.cancelled is True


def test_git_intelligence():
    status = get_git_status()
    assert "branch" in status
    assert "spoken" in status
    assert "is_clean" in status

    commits = get_recent_commits(limit=3)
    assert isinstance(commits, list)
    if commits:
        assert "hash" in commits[0]
        assert "message" in commits[0]


def test_web_researcher_offline_resilience():
    # Test instant answer on widely known entity
    res = research_topic("Alan Turing")
    assert "spoken" in res
    assert "display" in res
    if res["ok"]:
        assert "Turing" in res["summary"] or "Alan" in res["summary"]


def test_clipboard_intel():
    test_str = "JARVIS Test Token 42"
    assert set_clipboard_text(test_str) is True
    read_back = get_clipboard_text()
    assert read_back is not None
    assert "JARVIS Test Token 42" in read_back

    ins = inspect_clipboard()
    assert ins["has_content"] is True
    assert ins["words"] >= 4


def test_dev_assistant():
    # Analyze test_dom.js
    res = analyze_code_file("test_dom.js")
    assert res["ok"] is True
    assert res["total_lines"] > 0
    assert "spoken" in res
    assert "test_dom.js" in res["filename"]
