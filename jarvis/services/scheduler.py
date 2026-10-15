"""Persistent scheduler for reminders, recurring agent jobs and briefings.

Jobs live in SQLite, so they survive restarts. On startup, a job whose
``next_run`` passed while JARVIS was closed runs once if it was missed by
less than ``CATCH_UP_SECONDS``; otherwise it is recorded as missed and moved
to its next slot.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.app import Jarvis

log = logging.getLogger(__name__)
CATCH_UP_SECONDS = 6 * 3600
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


# ------------------------------------------------------------------ parsing
def _parse_clock(text: str) -> tuple[int, int] | None:
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b", text)
    if not m:
        if "noon" in text:
            return 12, 0
        if "midnight" in text:
            return 0, 0
        return None
    h, mnt, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if not ap and not m.group(2) and not re.search(r"\bat\b", text):
        return None  # a bare number is not a time
    if h > 23 or mnt > 59:
        return None
    return h, mnt


def parse_when(text: str, now: dt.datetime | None = None) -> float:
    """Parse natural expressions like 'in 10 minutes', 'at 5:30pm', 'tomorrow 9am', '2026-10-12 14:00'."""
    now = now or dt.datetime.now()
    t = text.lower().strip()
    m = re.search(r"\bin\s+(\d+(?:\.\d+)?|an?|one|half an?)\s*(sec|second|min|minute|hr|hour|day|week)s?\b", t)
    if m:
        qty_s = m.group(1)
        qty = 1.0 if qty_s in ("a", "an", "one") else 0.5 if qty_s.startswith("half") else float(qty_s)
        unit = m.group(2)
        secs = {"sec": 1, "second": 1, "min": 60, "minute": 60, "hr": 3600, "hour": 3600, "day": 86400,
                "week": 604800}[unit]
        return (now + dt.timedelta(seconds=qty * secs)).timestamp()
    iso = re.search(r"(\d{4}-\d{2}-\d{2})(?:[ t](\d{1,2}:\d{2}))?", t)
    if iso:
        base = dt.datetime.strptime(iso.group(1), "%Y-%m-%d")
        hm = (iso.group(2) or "09:00").split(":")
        return base.replace(hour=int(hm[0]), minute=int(hm[1])).timestamp()
    day = now.date()
    explicit_day = False
    if "tomorrow" in t:
        day = day + dt.timedelta(days=1)
        explicit_day = True
    else:
        for i, name in enumerate(_WEEKDAYS):
            if re.search(rf"\b{name}\b", t):
                delta = (i - now.weekday()) % 7 or 7
                day = day + dt.timedelta(days=delta)
                explicit_day = True
                break
    clock = _parse_clock(t)
    if clock is None:
        if explicit_day:
            clock = (9, 0)
        elif "tonight" in t:
            clock = (20, 0)
        else:
            raise ValueError(f"could not understand the time '{text}'")
    when = dt.datetime.combine(day, dt.time(*clock))
    if when <= now and not explicit_day:
        when += dt.timedelta(days=1)
    return when.timestamp()


def next_run(schedule: dict[str, Any], after: float) -> float | None:
    kind = schedule.get("type")
    if kind == "once":
        at = float(schedule["at"])
        return at if at > after else None
    if kind == "interval":
        secs = max(30.0, float(schedule["seconds"]))
        start = float(schedule.get("start", after))
        if start > after:
            return start
        n = int((after - start) // secs) + 1
        return start + n * secs
    if kind in ("daily", "weekly"):
        h, m = (int(x) for x in schedule.get("time", "09:00").split(":"))
        days = schedule.get("days") or list(range(7))
        base = dt.datetime.fromtimestamp(after)
        for offset in range(0, 8):
            d = base.date() + dt.timedelta(days=offset)
            if d.weekday() not in days:
                continue
            cand = dt.datetime.combine(d, dt.time(h, m)).timestamp()
            if cand > after:
                return cand
        return None
    raise ValueError(f"unknown schedule type {kind}")


def describe(schedule: dict[str, Any]) -> str:
    kind = schedule.get("type")
    if kind == "once":
        return "once at " + time.strftime("%Y-%m-%d %H:%M", time.localtime(schedule["at"]))
    if kind == "interval":
        secs = schedule["seconds"]
        return f"every {secs // 3600}h" if secs % 3600 == 0 else f"every {secs // 60} min"
    days = schedule.get("days")
    when = schedule.get("time", "09:00")
    if not days or len(days) == 7:
        return f"daily at {when}"
    return f"{', '.join(_WEEKDAYS[d][:3] for d in days)} at {when}"


# ---------------------------------------------------------------- scheduler
class Scheduler:
    def __init__(self, app: Jarvis) -> None:
        self.app = app
        self.db = app.db
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def add(self, name: str, kind: str, payload: dict[str, Any], schedule: dict[str, Any]) -> dict[str, Any]:
        nr = next_run(schedule, time.time() - 5)  # allow "now" for one-shot jobs
        if nr is None:
            raise ValueError("that time is in the past")
        cur = self.db.execute("INSERT INTO jobs(name, kind, payload, schedule, enabled, next_run, created) "
                              "VALUES (?,?,?,?,1,?,?)", (name, kind, json.dumps(payload), json.dumps(schedule), nr,
                                                         time.time()))
        self._wake.set()
        self.app.events.publish("jobs.changed")
        return {"id": cur.lastrowid, "name": name, "next_run": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(nr)),
                "schedule": describe(schedule)}

    def list(self, include_disabled: bool = True) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM jobs" + ("" if include_disabled else " WHERE enabled=1") +
                             " ORDER BY enabled DESC, next_run")
        for r in rows:
            r["payload"] = json.loads(r["payload"])
            r["schedule"] = json.loads(r["schedule"])
            r["schedule_text"] = describe(r["schedule"])
            r["next_run_text"] = time.strftime("%a %d %b %H:%M", time.localtime(r["next_run"])) if r["next_run"] else "-"
        return rows

    def runs(self, job_id: int, limit: int = 20) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM job_runs WHERE job_id=? ORDER BY id DESC LIMIT ?", (job_id, limit))

    def set_enabled(self, job_id: int, enabled: bool) -> None:
        row = self.db.one("SELECT schedule FROM jobs WHERE id=?", (job_id,))
        if not row:
            raise KeyError(job_id)
        nr = next_run(json.loads(row["schedule"]), time.time()) if enabled else None
        self.db.execute("UPDATE jobs SET enabled=?, next_run=? WHERE id=?", (int(enabled), nr, job_id))
        self._wake.set()
        self.app.events.publish("jobs.changed")

    def update(self, job_id: int, name: str | None = None, payload: dict[str, Any] | None = None,
               schedule: dict[str, Any] | None = None) -> None:
        row = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not row:
            raise KeyError(job_id)
        sched = schedule or json.loads(row["schedule"])
        self.db.execute("UPDATE jobs SET name=?, payload=?, schedule=?, next_run=? WHERE id=?",
                        (name or row["name"], json.dumps(payload) if payload else row["payload"], json.dumps(sched),
                         next_run(sched, time.time()), job_id))
        self._wake.set()
        self.app.events.publish("jobs.changed")

    def delete(self, job_id: int) -> bool:
        ok = self.db.execute("DELETE FROM jobs WHERE id=?", (job_id,)).rowcount > 0
        self.app.events.publish("jobs.changed")
        return ok

    def run_now(self, job_id: int) -> None:
        row = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if row:
            threading.Thread(target=self._execute, args=(row, False), daemon=True).start()

    # ---------------------------------------------------------------- loop
    def start(self) -> None:
        self._catch_up()
        self._thread = threading.Thread(target=self._loop, name="jarvis-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _catch_up(self) -> None:
        now = time.time()
        for row in self.db.query("SELECT * FROM jobs WHERE enabled=1 AND next_run IS NOT NULL AND next_run < ?", (now,)):
            if now - row["next_run"] > CATCH_UP_SECONDS:
                self.db.execute("INSERT INTO job_runs(job_id, started, finished, status, output) VALUES (?,?,?,?,?)",
                                (row["id"], now, now, "missed", "JARVIS was not running at the scheduled time"))
                self._advance(row, now)

    def _advance(self, row: dict[str, Any], now: float) -> None:
        sched = json.loads(row["schedule"])
        nr = next_run(sched, now)
        self.db.execute("UPDATE jobs SET next_run=?, enabled=? WHERE id=?",
                        (nr, int(nr is not None and bool(row["enabled"])), row["id"]))

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            due = self.db.query("SELECT * FROM jobs WHERE enabled=1 AND next_run IS NOT NULL AND next_run <= ?", (now,))
            for row in due:
                self._advance(row, now)
                threading.Thread(target=self._execute, args=(row, True), name=f"job-{row['id']}", daemon=True).start()
            if due:
                self.app.events.publish("jobs.changed")
            upcoming = self.db.one("SELECT MIN(next_run) AS n FROM jobs WHERE enabled=1 AND next_run IS NOT NULL")
            wait = 30.0
            if upcoming and upcoming["n"]:
                wait = max(0.2, min(30.0, upcoming["n"] - time.time()))
            self._wake.wait(wait)
            self._wake.clear()

    def _execute(self, row: dict[str, Any], scheduled: bool) -> None:
        started = time.time()
        run = self.db.execute("INSERT INTO job_runs(job_id, started, status) VALUES (?,?,?)",
                              (row["id"], started, "running")).lastrowid
        payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        status, output = "ok", ""
        try:
            output = self._dispatch(row["kind"], row["name"], payload)
        except Exception as exc:  # noqa: BLE001 - job failures are recorded, not raised
            log.exception("job %s failed", row["id"])
            status, output = "failed", f"{exc.__class__.__name__}: {exc}"
            self.app.notify("Scheduled job failed", f"{row['name']}: {exc}", speak=False)
        self.db.execute("UPDATE job_runs SET finished=?, status=?, output=? WHERE id=?",
                        (time.time(), status, (output or "")[:4000], run))
        self.db.execute("UPDATE jobs SET last_run=?, last_status=? WHERE id=?", (started, status, row["id"]))
        self.app.events.publish("jobs.changed")

    def _dispatch(self, kind: str, name: str, payload: dict[str, Any]) -> str:
        if kind == "reminder":
            msg = payload.get("message", name)
            self.app.notify("Reminder", msg, speak=True)
            return f"reminded: {msg}"
        if kind == "focus_end":
            self.app.notify("Focus session complete", payload.get("message", "Time for a break."), speak=True)
            return "focus session ended"
        if kind == "briefing":
            text = self.app.briefing()
            self.app.notify("Daily briefing", text[:300], speak=True)
            return text
        if kind == "agent":
            task = self.app.agent.submit(payload["goal"], source=f"schedule:{name}")
            final = self.app.agent.wait(task.id, timeout=float(payload.get("timeout", 1800)))
            if payload.get("notify", True):
                self.app.notify(f"Scheduled task: {name}", (final or {}).get("result", "")[:300] or "finished",
                                speak=False)
            return json.dumps({"task": task.id, "status": (final or {}).get("status")})
        raise ValueError(f"unknown job kind {kind}")
