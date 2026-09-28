"""Tests for the agentic planner, executor, and file authoring layer."""

import pytest

from core.agentic import ALLOWED_ACTIONS, sanitize_plan
from core.agentic_exec import execute_plan
from tools import file_authoring


# --------------------------------------------------------------------------- #
# Planner sanitisation
# --------------------------------------------------------------------------- #
def test_planner_only_allows_known_actions():
    plan = sanitize_plan({"steps": [{"action": "click_text", "target": "File"}]})
    assert plan["steps"][0]["action"] == "click_text"


def test_planner_rejects_unknown_actions():
    assert sanitize_plan({"steps": [{"action": "rm_rf_slash"}]}) is None
    assert sanitize_plan({"steps": [{"action": "format_disk"}]}) is None


def test_planner_rejects_empty_or_malformed_plans():
    assert sanitize_plan(None) is None
    assert sanitize_plan({}) is None
    assert sanitize_plan({"steps": []}) is None
    assert sanitize_plan({"steps": "not a list"}) is None


def test_planner_drops_steps_missing_required_arguments():
    # click_text with no target, type_text with no text -> nothing survives.
    assert sanitize_plan({"steps": [{"action": "click_text"}]}) is None
    assert sanitize_plan({"steps": [{"action": "type_text"}]}) is None
    assert sanitize_plan({"steps": [{"action": "create_file", "target": "a.txt"}]}) is None


def test_planner_preserves_multi_step_order():
    plan = sanitize_plan({"steps": [
        {"action": "click_text", "target": "Search"},
        {"action": "type_text", "text": "cats"},
        {"action": "press_keys", "keys": "enter"},
    ]})
    assert [s["action"] for s in plan["steps"]] == ["click_text", "type_text", "press_keys"]
    assert plan["steps"][1]["text"] == "cats"


def test_planner_keeps_clarification_only_when_it_is_the_sole_step():
    alone = sanitize_plan({"steps": [{"action": "needs_clarification"}], "question": "Which file?"})
    assert alone["steps"][0]["action"] == "needs_clarification"

    # Hedging alongside real work must not block the real work.

# --------------------------------------------------------------------------- #
# Executor behaviour
# --------------------------------------------------------------------------- #
def test_executor_delegates_pure_conversation():
    assert execute_plan({"steps": [{"action": "answer"}]}).get("delegate_to_llm") is True


def test_executor_returns_question_for_clarification():
    out = execute_plan({"steps": [{"action": "needs_clarification"}], "question": "Which one?"})
    assert out["spoken"] == "Which one?"
    assert out["needs_clarification"] is True


def test_executor_keeps_completed_work_when_trailing_answer_step(tmp_path, monkeypatch):
    """A trailing "answer" step must not discard the file we just wrote."""
    monkeypatch.setattr(file_authoring, "_SAFE_ROOTS", (tmp_path,))
    plan = {"steps": [
        {"action": "create_file", "target": str(tmp_path / "probe.txt"), "text": "hello"},
        {"action": "answer"},
    ], "reply": ""}
    out = execute_plan(plan)
    assert not out.get("delegate_to_llm")
    assert out.get("spoken")
    assert (tmp_path / "probe.txt").read_text(encoding="utf-8") == "hello"


def test_executor_click_failure_is_reported_not_faked(monkeypatch):
    """A target that cannot be located must never silently 'click the cursor'."""
    import core.agentic_exec as ex

    def boom(target, clicks=1, button="left"):
        return {"ok": False, "error": f"I couldn't find '{target}' on your screen."}

    monkeypatch.setattr(ex, "_click_text", boom)
    out = execute_plan({"steps": [{"action": "click_text", "target": "ghost"}]})
    assert out["failed"] is True
    assert "couldn't find" in out["spoken"].lower()


def test_executor_aborts_typing_after_failed_click(monkeypatch):
    """Text must never be typed into an unknown window."""
    import core.agentic_exec as ex

    monkeypatch.setattr(ex, "_click_text", lambda *a, **k: {"ok": False, "error": "nope"})
    typed = []
    monkeypatch.setattr(ex, "_run_remote", lambda *a, **k: typed.append(a) or {"ok": True})

    execute_plan({"steps": [
        {"action": "click_text", "target": "ghost"},
        {"action": "type_text", "text": "secret"},
    ]})
    assert not typed  # no type_text reached the machine

    mixed = sanitize_plan({"steps": [
        {"action": "needs_clarification"},
        {"action": "create_file", "target": "a.txt", "text": "hi"},
    ]})
    assert [s["action"] for s in mixed["steps"]] == ["create_file"]


def test_planner_caps_step_count():
    steps = [{"action": "press_keys", "keys": "enter"} for _ in range(30)]
    assert len(sanitize_plan({"steps": steps})["steps"]) <= 6


def test_every_allowed_action_is_documented():
    for action, description in ALLOWED_ACTIONS.items():
        assert description and action == action.lower()


# --------------------------------------------------------------------------- #
# File authoring
# --------------------------------------------------------------------------- #
def test_create_write_and_read_back(tmp_path, monkeypatch):
    monkeypatch.setattr(file_authoring, "_SAFE_ROOTS", (tmp_path,))
    name = str(tmp_path / "notes.txt")

    res = file_authoring.create_file(name, "line one\nline two\n")
    assert res["ok"] is True
    assert res["lines"] == 3
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "line one\nline two\n"


def test_create_refuses_to_clobber_without_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(file_authoring, "_SAFE_ROOTS", (tmp_path,))
    name = str(tmp_path / "notes.txt")
    file_authoring.create_file(name, "original")

    again = file_authoring.create_file(name, "replacement")
    assert again["ok"] is False
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "original"

    forced = file_authoring.create_file(name, "replacement", overwrite=True)
    assert forced["ok"] is True
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "replacement"


def test_append_adds_without_losing_existing_content(tmp_path, monkeypatch):
    monkeypatch.setattr(file_authoring, "_SAFE_ROOTS", (tmp_path,))
    name = str(tmp_path / "log.txt")
    file_authoring.create_file(name, "first")
    file_authoring.append_to_file(name, "second")
    body = (tmp_path / "log.txt").read_text(encoding="utf-8")
    assert "first" in body and "second" in body


def test_writes_are_sandboxed_outside_safe_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(file_authoring, "_SAFE_ROOTS", (tmp_path / "allowed",))
    blocked = file_authoring.create_file(str(tmp_path / "outside.txt"), "nope")
    assert blocked["ok"] is False
    assert not (tmp_path / "outside.txt").exists()


def test_oversized_content_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(file_authoring, "_SAFE_ROOTS", (tmp_path,))
    huge = "a" * (file_authoring._MAX_WRITE_BYTES + 10)
    assert file_authoring.create_file(str(tmp_path / "big.txt"), huge)["ok"] is False


def test_parse_quoted_create_extracts_name_and_content():
    name, content = file_authoring.parse_quoted_create(
        'create a file "notes.txt" containing "buy milk"'
    )
    assert name == "notes.txt"
    assert content == "buy milk"
