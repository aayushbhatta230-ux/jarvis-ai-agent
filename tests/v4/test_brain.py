"""Tool server (MCP over HTTP) and the Claude brain hand-off, with a fake claude process."""

from __future__ import annotations

import httpx
import pytest

from tests.v4.fakes import FakeProvider, reply


def _rpc(srv, method, params=None, mid=1, token=None):
    return httpx.post(srv.url, json={"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}},
                      headers={"Authorization": f"Bearer {token or srv.token}"}, timeout=20)


def test_tool_server_speaks_mcp(app, workdir):
    from jarvis.services.mcp_server import ToolServer

    srv = ToolServer(app)
    srv.start()
    try:
        assert httpx.post(srv.url, json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
        init = _rpc(srv, "initialize", {"protocolVersion": "2025-06-18"}).json()
        assert init["result"]["serverInfo"]["name"] == "jarvis"
        assert httpx.post(srv.url, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                          headers={"Authorization": f"Bearer {srv.token}"}).status_code == 202
        tools = _rpc(srv, "tools/list").json()["result"]["tools"]
        names = {t["name"] for t in tools}
        assert {"take_screenshot", "click_on_screen", "send_email", "play_media"} <= names
        assert "delegate_to_coding_agent" not in names
        (workdir / "a.txt").write_text("hello from jarvis")
        res = _rpc(srv, "tools/call", {"name": "read_file", "arguments": {"path": str(workdir / "a.txt")}}).json()
        assert not res["result"]["isError"] and "hello from jarvis" in res["result"]["content"][0]["text"]
        bad = _rpc(srv, "tools/call", {"name": "nope", "arguments": {}}).json()
        assert bad["result"]["isError"]
    finally:
        srv.stop()


def test_agent_uses_claude_when_available_and_falls_back(app, monkeypatch):
    app.config.set("agent.brain", "auto")
    calls = []

    def fake_run(task_id, goal, history, cancel, on_step, timeout=900):
        calls.append(goal)
        on_step("tool_call", "list_windows", {})
        return True, "You have Chrome and VS Code open."

    monkeypatch.setattr(app.claude, "available", lambda max_age=300: (True, "signed in"))
    monkeypatch.setattr(app.claude, "run", fake_run)
    run = app.agent.submit("which apps are open")
    row = app.agent.wait(run.id, 20)
    assert row["status"] == "completed" and "Chrome" in row["result"] and calls

    # Claude fails (e.g. usage limit) -> local agent finishes the job
    monkeypatch.setattr(app.claude, "run", lambda *a, **k: (False, "usage limit reached"))
    FakeProvider.scripts = {"fake": [reply(calls=[("task_complete", {"summary": "done locally", "verified": True})])]}
    run = app.agent.submit("which apps are open now")
    row = app.agent.wait(run.id, 20)
    assert row["status"] == "completed" and row["result"] == "done locally"


def test_local_agent_is_nudged_when_it_refuses(app):
    FakeProvider.scripts = {"fake": [
        reply("I'm sorry, I can't play videos on your screen."),
        lambda msgs: reply(calls=[("task_complete", {"summary": "nudged and acted", "verified": True})])
        if "You CAN do this" in msgs[-1]["content"] else reply("still refusing"),
    ]}
    run = app.agent.submit("play the video")
    row = app.agent.wait(run.id, 20)
    assert row["result"] == "nudged and acted"


def test_local_agent_gets_a_short_relevant_tool_list(app):
    from jarvis.agent.engine import TaskRun, select_groups
    from jarvis.core.cancel import CancelToken

    goal = "play starboy on youtube"
    run = TaskRun("t", goal, "user", CancelToken(), groups=select_groups(goal))
    names = [t.name for t in app.agent._tools_for(run, local=True)]
    assert "play_media" in names and len(names) <= 4 + 14


@pytest.mark.parametrize("text,addressed", [
    ("reservation", False), ("girls hostel", False), ("mummy kasam mummy ko samajhne mein", False),
    ("and and", False), ("what's on my screen", True), ("open chrome", True), ("can you hear me", True),
    ("I think the code has a bug in the login function somewhere", True),
])
def test_follow_up_ignores_side_conversation(text, addressed):
    from jarvis.voice.pipeline import looks_addressed

    assert looks_addressed(text) is addressed


def test_screen_answers_ignore_earlier_denials(app, monkeypatch):
    from PIL import Image

    app.history = [{"role": "user", "content": "can you see my screen"},
                   {"role": "assistant", "content": "No, I don't see your screen, sorry."}]
    monkeypatch.setattr(app.screen, "latest_frame", lambda max_age=0: (Image.new("RGB", (8, 8)), "Inbox - Gmail"))
    FakeProvider.scripts = {"fake": [lambda m: reply("You're in your Gmail inbox.")
                                     if not any("don't see your screen" in x.get("content", "") for x in m)
                                     else reply("poisoned")]}
    msgs = []
    app.events.subscribe("assistant.message", lambda e: msgs.append(e.data["text"]))
    app.handle_input("what's on my screen", "text")
    assert msgs[-1] == "You're in your Gmail inbox."
