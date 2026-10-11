"""Productivity tools: reminders, scheduled jobs, to-dos, focus sessions, drafts, briefings."""

from __future__ import annotations

import re
import time
from typing import Any

from jarvis.core import paths
from jarvis.services.scheduler import parse_when
from jarvis.tools.registry import ToolContext, ToolError, tool


def _ts(text: str) -> float:
    try:
        return parse_when(text)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


@tool("set_reminder", "Set a reminder that will be shown and spoken at a time "
      "('in 20 minutes', 'at 5pm', 'tomorrow 9am', 'friday at 14:00', '2026-10-20 10:00').",
      {"message": {"type": "string"}, "when": {"type": "string"}}, required=["message", "when"], category="write",
      group="productivity")
def set_reminder(ctx: ToolContext, message: str, when: str) -> dict[str, Any]:
    at = _ts(when)
    return ctx.app.scheduler.add(message[:60], "reminder", {"message": message}, {"type": "once", "at": at})


def _schedule_from(kind: str, time_of_day: str, every_minutes: int, days: list[str] | None, when: str) -> dict[str, Any]:
    names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    if kind == "once":
        return {"type": "once", "at": _ts(when)}
    if kind == "interval":
        if every_minutes < 1:
            raise ToolError("every_minutes must be >= 1")
        return {"type": "interval", "seconds": every_minutes * 60, "start": time.time() + every_minutes * 60}
    if not re.fullmatch(r"\d{1,2}:\d{2}", time_of_day or ""):
        raise ToolError("time_of_day must be HH:MM")
    day_idx = None
    if days:
        day_idx = sorted({names.index(d[:3].lower()) for d in days if d[:3].lower() in names})
        if "weekdays" in [d.lower() for d in days]:
            day_idx = [0, 1, 2, 3, 4]
    return {"type": "daily", "time": time_of_day, "days": day_idx}


@tool("schedule_task", "Schedule JARVIS to perform a goal automatically (once, daily at a time, on certain days, "
      "or every N minutes). Example goal: 'check disk space and warn me if C: is below 10 GB'.",
      {"name": {"type": "string"}, "goal": {"type": "string"},
       "kind": {"type": "string", "enum": ["once", "daily", "interval"]},
       "when": {"type": "string", "description": "for once: natural time"},
       "time_of_day": {"type": "string", "description": "HH:MM for daily"},
       "days": {"type": "array", "items": {"type": "string"}, "description": "e.g. ['mon','wed'] or ['weekdays']"},
       "every_minutes": {"type": "integer"}},
      required=["goal", "kind"], category="write", group="productivity")
def schedule_task(ctx: ToolContext, goal: str, kind: str, name: str = "", when: str = "", time_of_day: str = "",
                  days: list[str] | None = None, every_minutes: int = 0) -> dict[str, Any]:
    sched = _schedule_from(kind, time_of_day, every_minutes, days, when)
    return ctx.app.scheduler.add(name or goal[:60], "agent", {"goal": goal}, sched)


@tool("list_scheduled_jobs", "List reminders and scheduled jobs with next run times and last status.",
      group="productivity")
def list_scheduled_jobs(ctx: ToolContext) -> list[dict[str, Any]]:
    return [{"id": j["id"], "name": j["name"], "kind": j["kind"], "schedule": j["schedule_text"],
             "next_run": j["next_run_text"], "enabled": bool(j["enabled"]), "last_status": j["last_status"]}
            for j in ctx.app.scheduler.list()]


@tool("manage_scheduled_job", "Enable, disable, delete or run-now a scheduled job by id.",
      {"id": {"type": "integer"}, "action": {"type": "string", "enum": ["enable", "disable", "delete", "run_now"]}},
      required=["id", "action"], category="write", group="productivity")
def manage_scheduled_job(ctx: ToolContext, id: int, action: str) -> str:  # noqa: A002
    s = ctx.app.scheduler
    try:
        if action == "delete":
            if not s.delete(id):
                raise ToolError(f"no job {id}")
        elif action == "run_now":
            s.run_now(id)
        else:
            s.set_enabled(id, action == "enable")
    except KeyError as exc:
        raise ToolError(f"no job {id}") from exc
    return f"job {id}: {action} done"


@tool("add_todo", "Add a to-do item with optional due time, priority (1 high - 3 low) and project.",
      {"title": {"type": "string"}, "notes": {"type": "string"}, "due": {"type": "string"},
       "priority": {"type": "integer"}, "project": {"type": "string"}, "remind": {"type": "boolean"}},
      required=["title"], category="write", group="productivity")
def add_todo(ctx: ToolContext, title: str, notes: str = "", due: str = "", priority: int = 2, project: str = "",
             remind: bool = False) -> dict[str, Any]:
    due_ts = _ts(due) if due else None
    cur = ctx.app.db.execute("INSERT INTO todos(title, notes, due, priority, project, created) VALUES (?,?,?,?,?,?)",
                             (title, notes, due_ts, max(1, min(priority, 3)), project, time.time()))
    if remind and due_ts:
        ctx.app.scheduler.add(f"Due: {title}"[:60], "reminder", {"message": f"Due now: {title}"},
                              {"type": "once", "at": due_ts})
    ctx.app.events.publish("todos.changed")
    return {"id": cur.lastrowid, "title": title,
            "due": time.strftime("%Y-%m-%d %H:%M", time.localtime(due_ts)) if due_ts else None}


@tool("list_todos", "List to-do items (open by default), overdue first.",
      {"status": {"type": "string", "enum": ["open", "done", "all"]}, "project": {"type": "string"}},
      group="productivity")
def list_todos(ctx: ToolContext, status: str = "open", project: str = "") -> list[dict[str, Any]]:
    sql = "SELECT id, title, notes, due, priority, status, project FROM todos"
    conds, params = [], []
    if status != "all":
        conds.append("status=?")
        params.append(status)
    if project:
        conds.append("project LIKE ?")
        params.append(f"%{project}%")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY (due IS NULL), due, priority"
    rows = ctx.app.db.query(sql, params)
    now = time.time()
    for r in rows:
        r["overdue"] = bool(r["due"] and r["due"] < now and r["status"] == "open")
        r["due"] = time.strftime("%a %d %b %H:%M", time.localtime(r["due"])) if r["due"] else None
    return rows


@tool("complete_todo", "Mark a to-do as done (or reopen it).", {"id": {"type": "integer"}, "done": {"type": "boolean"}},
      required=["id"], category="write", group="productivity")
def complete_todo(ctx: ToolContext, id: int, done: bool = True) -> str:  # noqa: A002
    n = ctx.app.db.execute("UPDATE todos SET status=?, completed=? WHERE id=?",
                           ("done" if done else "open", time.time() if done else None, id)).rowcount
    if not n:
        raise ToolError(f"no to-do {id}")
    ctx.app.events.publish("todos.changed")
    return f"to-do {id} marked {'done' if done else 'open'}"


@tool("start_focus_session", "Start a focus session timer (Pomodoro); JARVIS announces when it ends.",
      {"minutes": {"type": "integer"}, "task": {"type": "string"}}, category="write", group="productivity")
def start_focus_session(ctx: ToolContext, minutes: int = 25, task: str = "") -> dict[str, Any]:
    minutes = max(1, min(minutes, 240))
    info = ctx.app.scheduler.add(f"Focus: {task or 'session'}"[:60], "focus_end",
                                 {"message": f"Focus session{' on ' + task if task else ''} complete. Take a break."},
                                 {"type": "once", "at": time.time() + minutes * 60})
    ctx.app.events.publish("focus.started", minutes=minutes, task=task)
    return info


@tool("save_draft", "Save a drafted note, report, email or message as a Markdown file in the drafts folder "
      "(does not send anything).",
      {"title": {"type": "string"}, "content": {"type": "string"}}, required=["title", "content"], category="write",
      group="productivity")
def save_draft(ctx: ToolContext, title: str, content: str) -> str:
    folder = paths.workspace_dir() / "drafts"
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "draft"
    path = folder / f"{time.strftime('%Y%m%d-%H%M')}-{slug}.md"
    path.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
    return f"draft saved to {path}"


@tool("daily_briefing", "Produce a briefing: date/time, open & overdue to-dos, upcoming reminders/jobs, recent task "
      "outcomes and system health.", group="productivity", timeout=60)
def daily_briefing(ctx: ToolContext) -> str:
    return ctx.app.briefing()


@tool("work_summary", "Summarise ongoing and unfinished work: running/paused/failed tasks and open to-dos, "
      "with a suggested next step.", group="productivity")
def work_summary(ctx: ToolContext) -> dict[str, Any]:
    db = ctx.app.db
    unfinished = db.query("SELECT id, goal, status, updated FROM tasks WHERE status IN "
                          "('running','paused','failed','interrupted','waiting') ORDER BY updated DESC LIMIT 10")
    todos = db.query("SELECT id, title, due, priority FROM todos WHERE status='open' ORDER BY (due IS NULL), due, "
                     "priority LIMIT 10")
    recent = db.query("SELECT goal, status FROM tasks WHERE status='completed' ORDER BY updated DESC LIMIT 5")
    return {"unfinished_tasks": unfinished, "open_todos": todos, "recently_completed": recent,
            "last_project": db.kv_get("last_project")}
