"""Model routing/fallback and the agent engine (planning, execution, recovery, persistence)."""

from __future__ import annotations

import json
import threading
import time

import pytest

from jarvis.providers.base import ProviderError, RateLimited
from jarvis.providers.router import NoModelAvailable
from tests.v4.fakes import FakeProvider, reply


# ---------------------------------------------------------------- router
def test_router_falls_back_and_cools_down(app):
    app.config.set("models.roles.chat", ["fake:m", "fake2:m"])
    FakeProvider.scripts = {"fake": [ProviderError("boom")], "fake2": [reply("from backup")]}
    events = []
    app.events.subscribe("model.fallback", lambda e: events.append(e.data))
    res = app.router.chat("chat", [{"role": "user", "content": "hi"}])
    assert res.text == "from backup" and res.provider == "fake2"
    assert events and events[0]["candidate"] == "fake:m"
    ok, why = app.router.candidate_ok(app.router.candidates("chat")[0], {"completion"})
    assert not ok and "cooling down" in why


def test_router_rate_limit_respects_retry_after(app):
    app.config.set("models.roles.chat", ["fake:m", "fake2:m"])
    FakeProvider.scripts = {"fake": [RateLimited("slow down", retry_after=120)], "fake2": [reply("ok")]}
    app.router.chat("chat", [{"role": "user", "content": "hi"}])
    assert app.router._cooldown["fake:m"] - time.time() > 100


def test_router_capability_filter_and_override(app):
    # vision requests skip models without vision
    app.config.set("models.roles.vision", ["fake:m", "fake:v"])
    FakeProvider.scripts = {"fake:v": [reply("I see a window")]}
    res = app.router.chat("vision", [{"role": "user", "content": "look", "images": ["AAAA"]}])
    assert res.model == "v"
    app.config.set("models.override", "fake2:m")
    assert app.router.resolve("chat").provider == "fake2"


def test_router_unhealthy_provider_and_no_model(app):
    FakeProvider.healthy = {"fake": False}
    app.config.set("models.roles.chat", ["fake:m"])
    with pytest.raises(NoModelAvailable, match="fake down"):
        app.router.chat("chat", [{"role": "user", "content": "hi"}])


def test_router_does_not_splice_after_partial_stream(app, monkeypatch):
    app.config.set("models.roles.chat", ["fake:m", "fake2:m"])
    prov = app.router.providers["fake"]

    def stream_then_fail(messages, model, tools=None, on_text=None, **kw):
        on_text("half an ans")
        raise ProviderError("connection dropped")

    monkeypatch.setattr(prov, "chat", stream_then_fail)
    FakeProvider.scripts = {"fake2": [reply("second model answer")]}
    seen = []
    with pytest.raises(ProviderError):
        app.router.chat("chat", [{"role": "user", "content": "x"}], on_text=seen.append)
    assert "".join(seen) == "half an ans"  # no second answer spliced onto the first


# ----------------------------------------------------------------- agent
def _run(app, goal, timeout=30):
    run = app.agent.submit(goal)
    row = app.agent.wait(run.id, timeout)
    return run, row


def test_agent_plans_executes_verifies(app, workdir):
    target = workdir / "out.txt"
    FakeProvider.scripts = {"fake": [
        reply(calls=[("update_plan", {"steps": [{"title": "write", "status": "active"},
                                                {"title": "verify", "status": "pending"}]})]),
        reply(calls=[("write_file", {"path": str(target), "content": "hi"})]),
        reply(calls=[("read_file", {"path": str(target)})]),
        reply(calls=[("task_complete", {"summary": "Wrote and checked out.txt", "verified": True,
                                        "evidence": "read_file shows hi"})]),
    ]}
    run, row = _run(app, f"write hi into {target}")
    assert row["status"] == "completed", row
    assert target.read_text() == "hi"
    assert json.loads(row["plan"])[0]["title"] == "write"
    kinds = [s["kind"] for s in app.db.query("SELECT kind FROM task_steps WHERE task_id=?", (run.id,))]
    assert kinds.count("tool") == 2 and kinds[0] == "plan" and kinds[-1] == "result"


def test_agent_marks_unverified_side_effects(app, workdir):
    FakeProvider.scripts = {"fake": [
        reply(calls=[("write_file", {"path": str(workdir / "u.txt"), "content": "x"})]),
        reply("Done, file written."),
    ]}
    _, row = _run(app, "write a file")
    assert row["status"] == "completed_unverified"


def test_agent_recovers_from_failure_and_replans(app, workdir):
    FakeProvider.scripts = {"fake": [
        reply(calls=[("read_file", {"path": str(workdir / "missing.txt")})]),
        lambda msgs: reply(calls=[("list_directory", {"path": str(workdir)})])
        if "ERROR" in msgs[-1]["content"] else reply("unexpected"),
        reply(calls=[("task_complete", {"summary": "File does not exist; listed folder instead", "verified": True})]),
    ]}
    _, row = _run(app, "read missing.txt")
    assert row["status"] == "completed"


def test_agent_blocks_identical_failing_calls_and_stops_streak(app, workdir):
    bad = ("read_file", {"path": str(workdir / "nope.txt")})
    FakeProvider.scripts = {"fake": [reply(calls=[bad]) for _ in range(8)]}
    run, row = _run(app, "keep failing")
    assert row["status"] == "failed"
    results = [s["result"] for s in app.db.query("SELECT result FROM task_steps WHERE task_id=? AND kind='tool'",
                                                  (run.id,))]
    assert any("already failed twice" in r for r in results)
    assert len(results) == 5  # stopped by the failure-streak guard


def test_agent_parses_text_tool_calls(app, workdir):
    FakeProvider.scripts = {"fake": [
        reply('<tool_call>\n{"name": "list_directory", "arguments": {"path": "%s"}}\n</tool_call>'
              % str(workdir).replace("\\", "\\\\")),
        reply(calls=[("task_complete", {"summary": "listed", "verified": True})]),
    ]}
    run, row = _run(app, "list my work folder")
    assert row["status"] == "completed"
    assert app.db.one("SELECT tool FROM task_steps WHERE task_id=? AND kind='tool'", (run.id,))["tool"] == \
        "list_directory"


def test_agent_cancel_midway(app):
    started = threading.Event()

    def slow(messages):
        started.set()
        time.sleep(1.0)
        return reply(calls=[("list_directory", {"path": "."})])

    FakeProvider.scripts = {"fake": [slow for _ in range(20)]}
    run = app.agent.submit("long job")
    started.wait(5)
    app.agent.cancel(run.id)
    row = app.agent.wait(run.id, 10)
    assert row["status"] == "cancelled"


def test_agent_ask_user_roundtrip(app):
    FakeProvider.scripts = {"fake": [
        reply(calls=[("ask_user", {"question": "Which folder?"})]),
        lambda msgs: reply(calls=[("task_complete", {"summary": f"user said {msgs[-1]['content']}",
                                                     "verified": True})]),
    ]}
    asked = threading.Event()
    app.events.subscribe("agent.question", lambda e: asked.set())
    run = app.agent.submit("organise files")
    assert asked.wait(5)
    assert app.agent.answer("Downloads")
    row = app.agent.wait(run.id, 10)
    assert row["status"] == "completed" and "Downloads" in row["result"]


def test_interrupted_task_can_resume_from_checkpoint(app, config, workdir):
    from jarvis.agent.engine import AgentEngine

    FakeProvider.scripts = {"fake": [reply(calls=[("list_directory", {"path": str(workdir)})]),
                                     reply(calls=[("task_complete", {"summary": "first run", "verified": True})])]}
    run, _ = _run(app, "inspect folder")
    # simulate a crash mid-task: mark as running, then restart the engine
    app.db.execute("UPDATE tasks SET status='running' WHERE id=?", (run.id,))
    engine2 = AgentEngine(app)
    assert app.db.one("SELECT status FROM tasks WHERE id=?", (run.id,))["status"] == "interrupted"
    FakeProvider.scripts = {"fake": [lambda msgs: reply(calls=[("task_complete", {
        "summary": "resumed with %d prior messages" % len(msgs), "verified": True})])]}
    resumed = engine2.resume(run.id)
    resumed.done.wait(10)
    row = app.db.one("SELECT status, result FROM tasks WHERE id=?", (run.id,))
    assert row["status"] == "completed" and "resumed with" in row["result"]
    assert int(row["result"].split()[2]) > 3  # checkpointed conversation was restored
    engine2.shutdown()


def test_agent_no_model_fails_cleanly(app):
    FakeProvider.healthy = {"fake": False}
    _, row = _run(app, "anything")
    assert row["status"] == "failed" and "No usable model" in row["error"]


def test_select_groups():
    from jarvis.agent.engine import select_groups

    assert "code" in select_groups("fix the failing tests in my repo")
    assert {"screen", "desktop"} <= select_groups("what's on my screen")
    assert "productivity" in select_groups("remind me tomorrow")
