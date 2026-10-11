"""Memory persistence/retrieval and background-job scheduling."""

from __future__ import annotations

import datetime as dt
import json
import threading
import time

from jarvis.services.scheduler import describe, next_run, parse_when


# ------------------------------------------------------------------ memory
def test_memory_add_dedupe_search_update_delete(app):
    m = app.memory
    a = m.add("User prefers dark themes in every editor", kind="preference", importance=0.9)
    b = m.add("user prefers dark themes in every editor.", kind="preference")
    assert a["id"] == b["id"] and b["status"] == "refreshed"
    m.add("The JARVIS project lives in C:/Users/joshi/JARVIS", kind="project", project="C:/Users/joshi/JARVIS")
    m.add("Fixed the CUDA error by reinstalling the driver", kind="fix")
    hits = m.search("which theme does the user like")
    assert hits and "dark themes" in hits[0]["content"]
    assert m.search("CUDA driver error")[0]["kind"] == "fix"
    m.update(a["id"], content="User prefers light themes now")
    assert "light" in m.search("themes")[0]["content"]
    assert m.delete(a["id"])
    assert not any("light" in r["content"] for r in m.list())


def test_memory_persists_across_restart(app, config):
    from jarvis.core.db import Database

    app.memory.add("Favourite editor is VS Code", kind="preference", importance=0.8)
    db2 = Database()
    rows = db2.query("SELECT content FROM memories")
    assert any("VS Code" in r["content"] for r in rows)
    db2.close()


def test_context_block_includes_core_profile(app):
    app.memory.add("Address the user formally", kind="preference", importance=0.9)
    app.memory.add("Meeting notes are in OneDrive", kind="note")
    block = app.memory.context_block("where are my meeting notes")
    assert "Address the user formally" in block and "OneDrive" in block


def test_memory_export_import_and_legacy(app, tmp_path):
    app.memory.add("Exported fact", kind="fact")
    path = app.memory.export(tmp_path / "mem.json")
    assert json.loads(path.read_text())["memories"]
    app.memory.delete(app.memory.list()[0]["id"])
    assert app.memory.import_file(path) == 1
    legacy = tmp_path / "legacy"
    (legacy / "memory").mkdir(parents=True)
    (legacy / "database").mkdir()
    (legacy / "memory" / "contacts.json").write_text(json.dumps({"mum": "mum@mail.com", "x": "x@example.com"}))
    (legacy / "database" / "notes.json").write_text(json.dumps([{"title": "Todo", "content": "buy milk"}]))
    (legacy / "memory" / "history.json").write_text(json.dumps([{"role": "user", "text": "hello", "ts": "t"}]))
    assert app.memory.import_legacy(legacy) == 2
    assert app.memory.import_legacy(legacy) == 0  # only once


def test_conversation_messages(app):
    app.memory.add_message("s1", "user", "hi")
    app.memory.add_message("s1", "assistant", "hello")
    assert [m["role"] for m in app.memory.recent_messages("s1")] == ["user", "assistant"]


# --------------------------------------------------------------- scheduler
def test_parse_when_variants():
    now = dt.datetime(2026, 10, 11, 20, 0)  # Sunday
    assert parse_when("in 10 minutes", now) == (now + dt.timedelta(minutes=10)).timestamp()
    assert parse_when("in an hour", now) == (now + dt.timedelta(hours=1)).timestamp()
    assert dt.datetime.fromtimestamp(parse_when("at 9pm", now)).hour == 21
    t = dt.datetime.fromtimestamp(parse_when("at 7am", now))
    assert t.day == 12 and t.hour == 7  # already past today -> tomorrow
    t = dt.datetime.fromtimestamp(parse_when("tomorrow at 9:30", now))
    assert (t.day, t.hour, t.minute) == (12, 9, 30)
    t = dt.datetime.fromtimestamp(parse_when("friday 2pm", now))
    assert t.weekday() == 4 and t.hour == 14
    assert dt.datetime.fromtimestamp(parse_when("2026-12-01 10:15", now)).month == 12


def test_next_run_and_describe():
    base = dt.datetime(2026, 10, 11, 8, 0).timestamp()
    nr = next_run({"type": "daily", "time": "08:30"}, base)
    assert dt.datetime.fromtimestamp(nr).strftime("%H:%M") == "08:30"
    nr = next_run({"type": "daily", "time": "08:30", "days": [0]}, base)  # Mondays only
    assert dt.datetime.fromtimestamp(nr).weekday() == 0
    assert next_run({"type": "interval", "seconds": 600, "start": base}, base + 1) == base + 600
    assert next_run({"type": "once", "at": base}, base + 100) is None
    assert describe({"type": "interval", "seconds": 1800}) == "every 30 min"


def test_scheduler_runs_due_reminder_and_records_history(app):
    notes = []
    app.events.subscribe("notify", lambda e: notes.append(e.data))
    app.scheduler.start()
    job = app.scheduler.add("tea", "reminder", {"message": "Tea is ready"}, {"type": "once", "at": time.time() + 1})
    for _ in range(60):
        if notes:
            break
        time.sleep(0.1)
    assert notes and notes[0]["message"] == "Tea is ready"
    time.sleep(0.3)
    runs = app.scheduler.runs(job["id"])
    assert runs and runs[0]["status"] == "ok"
    row = app.db.one("SELECT enabled FROM jobs WHERE id=?", (job["id"],))
    assert row["enabled"] == 0  # one-shot disables itself


def test_scheduler_persists_and_catches_up(app):
    from jarvis.services.scheduler import Scheduler

    long_missed = app.scheduler.add("old", "reminder", {"message": "old"}, {"type": "daily", "time": "03:00"})
    app.db.execute("UPDATE jobs SET next_run=? WHERE id=?", (time.time() - 10 * 3600, long_missed["id"]))
    s2 = Scheduler(app)  # simulates a restart
    s2._catch_up()
    runs = s2.runs(long_missed["id"])
    assert runs and runs[0]["status"] == "missed"
    assert app.db.one("SELECT next_run FROM jobs WHERE id=?", (long_missed["id"],))["next_run"] > time.time()


def test_scheduler_enable_disable_delete_and_agent_job(app):
    from tests.v4.fakes import FakeProvider, reply

    job = app.scheduler.add("daily check", "agent", {"goal": "say hi", "notify": False},
                            {"type": "daily", "time": "23:59"})
    app.scheduler.set_enabled(job["id"], False)
    assert not app.scheduler.list()[0]["enabled"]
    FakeProvider.scripts = {"fake": [reply(calls=[("task_complete", {"summary": "hi", "verified": True})])]}
    done = threading.Event()
    app.events.subscribe("agent.done", lambda e: done.set())
    app.scheduler.run_now(job["id"])
    assert done.wait(10)
    time.sleep(0.3)
    assert app.scheduler.runs(job["id"])[0]["status"] == "ok"
    assert app.scheduler.delete(job["id"])


def test_productivity_tools(app):
    r = app.tools.execute("add_todo", {"title": "Submit report", "due": "in 2 hours", "priority": 1})
    assert r.ok
    todos = app.tools.execute("list_todos", {})
    assert "Submit report" in todos.output
    assert app.tools.execute("complete_todo", {"id": r.data["id"]}).ok
    rem = app.tools.execute("set_reminder", {"message": "stretch", "when": "in 5 minutes"})
    assert rem.ok and rem.data["schedule"].startswith("once")
    assert app.tools.execute("set_reminder", {"message": "x", "when": "whenever"}).ok is False
    brief = app.tools.execute("daily_briefing", {})
    assert brief.ok and "It's" in brief.output
