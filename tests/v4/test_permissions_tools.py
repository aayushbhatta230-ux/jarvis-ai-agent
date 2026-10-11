"""Permission enforcement, tool validation, registry execution and file/shell tools."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from jarvis.core.permissions import PermissionDenied
from jarvis.tools.registry import Tool, ToolError, validate


def test_validate_coerces_and_rejects():
    schema = {"type": "object", "required": ["path"], "properties": {
        "path": {"type": "string"}, "n": {"type": "integer"}, "on": {"type": "boolean"},
        "mode": {"type": "string", "enum": ["a", "b"]}, "xs": {"type": "array"}}}
    out = validate(schema, {"path": "x", "n": "5", "on": "yes", "xs": "a, b", "junk": 1})
    assert out == {"path": "x", "n": 5, "on": True, "xs": ["a", "b"]}
    with pytest.raises(ToolError, match="missing required"):
        validate(schema, {"n": 1})
    with pytest.raises(ToolError, match="must be integer"):
        validate(schema, {"path": "x", "n": "five"})
    with pytest.raises(ToolError, match="one of"):
        validate(schema, {"path": "x", "mode": "c"})
    with pytest.raises(ToolError, match="must be integer"):
        validate(schema, {"path": "x", "n": True})


def test_registry_reports_unknown_and_invalid(app):
    r = app.tools.execute("no_such_tool", {})
    assert not r.ok and "unknown tool" in r.error
    r = app.tools.execute("read_file", {})
    assert not r.ok and "missing required argument 'path'" in r.error


def test_every_tool_has_valid_schema(app):
    assert len(app.tools.tools) > 60
    for t in app.tools.tools.values():
        assert t.parameters["type"] == "object"
        assert t.category in ("read", "write", "execute", "system", "external", "destructive"), t.name
        for req in t.parameters["required"]:
            assert req in t.parameters["properties"], (t.name, req)
        assert t.description


def test_policy_deny_blocks_and_audits(app, workdir):
    app.config.set("permissions.write", "deny")
    r = app.tools.execute("write_file", {"path": str(workdir / "a.txt"), "content": "x"})
    assert not r.ok and "not permitted" in r.error
    assert not (workdir / "a.txt").exists()
    row = app.db.one("SELECT decision FROM audit ORDER BY id DESC LIMIT 1")
    assert row["decision"] == "denied-by-policy"


def test_ask_policy_approve_and_deny(app, workdir):
    app.config.set("permissions.destructive", "ask")
    target = workdir / "victim.txt"
    target.write_text("x")

    def answer(granted):
        def handler(ev):
            threading.Timer(0.1, lambda: app.permissions.respond(ev.data["id"], granted)).start()
        return handler

    unsub = app.events.subscribe("permission.request", answer(False))
    r = app.tools.execute("delete_path", {"path": str(target)})
    unsub()
    assert not r.ok and target.exists()

    import send2trash

    deleted = []
    orig = send2trash.send2trash
    send2trash.send2trash = lambda p: (deleted.append(p), Path(p).unlink())
    try:
        unsub = app.events.subscribe("permission.request", answer(True))
        r = app.tools.execute("delete_path", {"path": str(target)})
        unsub()
    finally:
        send2trash.send2trash = orig
    assert r.ok, r.error
    assert deleted and not target.exists()


def test_ask_times_out_to_deny(app, workdir):
    app.config.set("permissions.system", "ask")
    app.config.set("permissions.ask_timeout_seconds", 0.3)
    t = time.time()
    r = app.tools.execute("lock_workstation", {})
    assert not r.ok and time.time() - t < 3


def test_dangerous_command_escalates_to_destructive(app, workdir):
    app.config.set("permissions.destructive", "deny")
    r = app.tools.execute("run_command", {"command": f"Remove-Item -Recurse -Force '{workdir}'"})
    assert not r.ok and "destructive" in r.error
    assert workdir.exists()


def test_write_outside_allowed_roots_is_refused(app, tmp_path):
    app.config.set("permissions.allowed_roots", [str(tmp_path / "only_here")])
    with pytest.raises(PermissionDenied):
        app.permissions.check_path(tmp_path / "elsewhere" / "x.txt", write=True)
    r = app.tools.execute("write_file", {"path": str(tmp_path / "elsewhere" / "x.txt"), "content": "x"})
    assert not r.ok and "outside the allowed folders" in r.error


def test_file_tools_roundtrip_with_snapshot_restore(app, workdir):
    f = workdir / "notes" / "a.txt"
    assert app.tools.execute("write_file", {"path": str(f), "content": "alpha\nbeta\n"}).ok
    r = app.tools.execute("write_file", {"path": str(f), "content": "zzz"})
    assert not r.ok and "overwrite=true" in r.error
    assert app.tools.execute("edit_file", {"path": str(f), "old_text": "beta", "new_text": "gamma"}).ok
    assert f.read_text() == "alpha\ngamma\n"
    r = app.tools.execute("edit_file", {"path": str(f), "old_text": "nope", "new_text": "x"})
    assert not r.ok and "not found" in r.error
    assert app.tools.execute("restore_file", {"path": str(f)}).ok
    assert f.read_text() == "alpha\nbeta\n"
    r = app.tools.execute("read_file", {"path": str(f), "start_line": 2, "end_line": 2})
    assert r.ok and "beta" in r.output and "alpha" not in r.output
    assert app.tools.execute("move_path", {"source": str(f), "destination": str(workdir / "b.txt")}).ok
    found = app.tools.execute("search_files", {"root": str(workdir), "name": "*.txt", "contains": "beta"})
    assert found.ok and found.data["total_matches"] == 1


def test_run_command_and_timeout(app, workdir):
    r = app.tools.execute("run_command", {"command": "Write-Output 'hello jarvis'", "cwd": str(workdir)})
    assert r.ok and "hello jarvis" in r.output
    r = app.tools.execute("run_command", {"command": "exit 3"})
    assert not r.ok and r.data["exit_code"] == 3
    r = app.tools.execute("run_command", {"command": "Start-Sleep 30", "timeout": 5})
    assert not r.ok and "timed out" in r.error


def test_tool_timeout_and_crash_are_contained(app):
    def slow(ctx):
        time.sleep(3)
        return "late"

    def boom(ctx):
        raise RuntimeError("kaboom")

    schema = {"type": "object", "properties": {}, "required": []}
    app.tools.register(Tool("slow_tool", "slow", schema, "read", slow, timeout=0.5))
    app.tools.register(Tool("boom_tool", "boom", schema, "read", boom))
    r = app.tools.execute("slow_tool", {})
    assert not r.ok and "timed out" in r.error
    r = app.tools.execute("boom_tool", {})
    assert not r.ok and "kaboom" in r.error


def test_background_process_lifecycle(app, workdir):
    r = app.tools.execute("start_background_process", {
        "command": "while ($true) { Write-Output tick; Start-Sleep -Milliseconds 300 }", "wait_seconds": 1.5})
    assert r.ok, r.error
    pid = r.data["id"]
    out = app.tools.execute("process_output", {"id": pid})
    assert "tick" in out.output
    assert app.tools.execute("stop_background_process", {"id": pid}).ok


def test_remember_refuses_secrets(app):
    r = app.tools.execute("remember", {"content": "my api key is sk-ant-abcdefghijklmnopqrstuvwxyz123"})
    assert not r.ok and "secret" in r.error
