"""Side panels: Activity, Memory, Schedule, System and Settings."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from jarvis.core import paths, secrets
from jarvis.ui import theme

if TYPE_CHECKING:
    from jarvis.app import Jarvis


class _Runner(QObject):
    done = Signal(object, object)


def run_bg(fn: Callable[[], Any], on_done: Callable[[Any, Exception | None], None]) -> None:
    """Run ``fn`` on a worker thread and deliver (result, error) on the GUI thread."""
    runner = _Runner()
    runner.done.connect(lambda r, e: (on_done(r, e), runner.deleteLater()))

    def work() -> None:
        try:
            runner.done.emit(fn(), None)
        except Exception as exc:  # noqa: BLE001
            runner.done.emit(None, exc)

    run_bg._keep = getattr(run_bg, "_keep", set())  # type: ignore[attr-defined]
    run_bg._keep.add(runner)  # type: ignore[attr-defined]
    runner.destroyed.connect(lambda: run_bg._keep.discard(runner))  # type: ignore[attr-defined]
    threading.Thread(target=work, daemon=True).start()


def section(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("Section")
    return lbl


def button(text: str, cb: Callable[[], None], obj: str = "") -> QPushButton:
    b = QPushButton(text)
    if obj:
        b.setObjectName(obj)
    b.clicked.connect(cb)
    return b


STATUS_COLOR = {"completed": theme.GREEN, "completed_unverified": theme.AMBER, "failed": theme.RED,
                "cancelled": theme.DIM, "running": theme.CYAN, "queued": theme.DIM, "waiting": theme.AMBER,
                "paused": theme.DIM, "interrupted": theme.AMBER, "ok": theme.GREEN, "error": theme.RED,
                "ready": theme.GREEN, "degraded": theme.AMBER, "unavailable": theme.RED, "missed": theme.AMBER}


# ======================================================================= Activity
class ActivityPanel(QWidget):
    def __init__(self, app: Jarvis) -> None:
        super().__init__()
        self.app = app
        self.current: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.addWidget(section("Current task"))
        self.goal = QLabel("No task running.")
        self.goal.setWordWrap(True)
        self.goal.setStyleSheet("font-size: 13px; font-weight: 600;")
        lay.addWidget(self.goal)
        self.meta = QLabel("")
        self.meta.setObjectName("Muted")
        lay.addWidget(self.meta)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        lay.addWidget(self.progress)
        self.plan = QListWidget()
        self.plan.setMaximumHeight(130)
        lay.addWidget(self.plan)
        row = QHBoxLayout()
        self.pause_btn = button("Pause", self._pause)
        self.cancel_btn = button("Cancel", self._cancel, "Danger")
        self.resume_btn = button("Resume selected", self._resume)
        for b in (self.pause_btn, self.cancel_btn, self.resume_btn):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(section("Live steps"))
        self.steps = QListWidget()
        self.steps.setWordWrap(True)
        lay.addWidget(self.steps, 3)
        lay.addWidget(section("Task history"))
        self.history = QTreeWidget()
        self.history.setHeaderLabels(["Task", "Status", "When"])
        self.history.setColumnWidth(0, 300)
        self.history.itemDoubleClicked.connect(self._show_task)
        lay.addWidget(self.history, 2)
        self.refresh()

    def refresh(self) -> None:
        self.history.clear()
        for r in self.app.db.query("SELECT id, goal, status, updated FROM tasks ORDER BY updated DESC LIMIT 60"):
            it = QTreeWidgetItem([r["goal"][:120], r["status"], time.strftime("%d %b %H:%M", time.localtime(r["updated"]))])
            it.setData(0, Qt.ItemDataRole.UserRole, r["id"])
            it.setForeground(1, QColor(STATUS_COLOR.get(r["status"], theme.DIM)))
            self.history.addTopLevelItem(it)
        self._update_buttons()

    def _update_buttons(self) -> None:
        run = self.app.agent.runs.get(self.current or "")
        active = bool(run and run.status not in ("completed", "completed_unverified", "failed", "cancelled"))
        self.pause_btn.setEnabled(active)
        self.cancel_btn.setEnabled(active)
        self.pause_btn.setText("Resume" if run and run.status == "paused" else "Pause")

    def on_event(self, topic: str, d: dict[str, Any]) -> None:
        if topic == "agent.task":
            self.current = d["task_id"]
            self.goal.setText(d["goal"])
            self.meta.setText(f"{d['status']} · step {d['step']} · {d.get('model') or 'selecting model'}")
            self._set_plan(d.get("plan") or [])
            self._update_buttons()
            if d["status"] in ("completed", "completed_unverified", "failed", "cancelled"):
                self.refresh()
        elif topic == "agent.plan" and d["task_id"] == self.current:
            self._set_plan(d["plan"])
        elif topic == "agent.step":
            icon = {"tool": "⚙", "thought": "💭", "plan": "☰", "result": "✓", "question": "?"}.get(d["kind"], "·")
            text = f"{icon} {d.get('tool') or d['kind']}  {d.get('args', '')[:120]}\n   {d['result'][:300]}"
            it = QListWidgetItem(text)
            it.setForeground(QColor(STATUS_COLOR.get(d["status"], theme.TEXT)) if d["status"] == "error" else
                             QColor(theme.TEXT))
            self.steps.addItem(it)
            if self.steps.count() > 300:
                self.steps.takeItem(0)
            self.steps.scrollToBottom()
        elif topic == "tool.start":
            self.meta.setText(f"running {d['tool']} ({d['category']})")
        elif topic == "tool.progress":
            self.meta.setText(d["message"])

    def _set_plan(self, plan: list[dict[str, Any]]) -> None:
        self.plan.clear()
        done = 0
        for s in plan:
            mark = {"done": "✓", "active": "▶", "failed": "✕", "skipped": "–"}.get(s.get("status"), "○")
            it = QListWidgetItem(f"{mark}  {s.get('title', '')}")
            if s.get("status") == "done":
                done += 1
                it.setForeground(QColor(theme.DIM))
            self.plan.addItem(it)
        self.progress.setRange(0, max(1, len(plan)))
        self.progress.setValue(done)

    def _pause(self) -> None:
        run = self.app.agent.runs.get(self.current or "")
        if not run:
            return
        if run.status == "paused":
            self.app.agent.unpause(run.id)
        else:
            self.app.agent.pause(run.id)
        self._update_buttons()

    def _cancel(self) -> None:
        if self.current:
            self.app.agent.cancel(self.current)

    def _resume(self) -> None:
        it = self.history.currentItem()
        if not it:
            return
        tid = it.data(0, Qt.ItemDataRole.UserRole)
        try:
            self.app.agent.resume(tid)
        except KeyError:
            pass

    def _show_task(self, item: QTreeWidgetItem) -> None:
        tid = item.data(0, Qt.ItemDataRole.UserRole)
        task = self.app.db.one("SELECT * FROM tasks WHERE id=?", (tid,))
        steps = self.app.db.query("SELECT kind, tool, args, result, status FROM task_steps WHERE task_id=? ORDER BY id",
                                  (tid,))
        text = f"Goal: {task['goal']}\nStatus: {task['status']}\nModel: {task['model']}\n\nResult:\n{task['result'] or ''}\n"
        if task["error"]:
            text += f"\nError: {task['error']}\n"
        text += "\n--- steps ---\n" + "\n".join(
            f"[{s['status']}] {s['kind']} {s['tool'] or ''} {s['args'] or ''}\n    {(s['result'] or '')[:500]}" for s in steps)
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Task details")
        dlg.setText(task["goal"][:200])
        dlg.setDetailedText(text)
        dlg.exec()


# ========================================================================= Memory
class MemoryPanel(QWidget):
    def __init__(self, app: Jarvis) -> None:
        super().__init__()
        self.app = app
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 4)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search memories…")
        self.search.returnPressed.connect(self.refresh)
        self.kind = QComboBox()
        self.kind.addItems(["all", "preference", "fact", "project", "decision", "routine", "fix", "goal", "contact",
                            "note", "task_outcome"])
        self.kind.currentTextChanged.connect(lambda _: self.refresh())
        row.addWidget(self.search, 3)
        row.addWidget(self.kind, 1)
        lay.addLayout(row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Kind", "Memory", "Updated"])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(1, 380)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        lay.addWidget(self.table, 1)
        btns = QHBoxLayout()
        for text, cb in (("Add", self._add), ("Edit", self._edit), ("Delete", self._delete), ("Export", self._export),
                         ("Import", self._import), ("Backup DB", self._backup), ("Restore DB", self._restore),
                         ("Re-embed", self._reembed)):
            btns.addWidget(button(text, cb, "Danger" if text == "Delete" else ""))
        lay.addLayout(btns)
        self.info = QLabel("")
        self.info.setObjectName("Muted")
        lay.addWidget(self.info)
        self.refresh()

    def refresh(self) -> None:
        q = self.search.text().strip()
        kind = "" if self.kind.currentText() == "all" else self.kind.currentText()
        rows = self.app.memory.search(q, k=100, kinds=[kind] if kind else None, min_score=0.0) if q else \
            self.app.memory.list(kind=kind)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            k = QTableWidgetItem(r["kind"])
            k.setData(Qt.ItemDataRole.UserRole, r["id"])
            self.table.setItem(i, 0, k)
            self.table.setItem(i, 1, QTableWidgetItem(r["content"]))
            upd = r.get("updated")
            self.table.setItem(i, 2, QTableWidgetItem(time.strftime("%d %b %y", time.localtime(upd)) if upd else ""))
        self.table.resizeRowsToContents()
        total = self.app.db.one("SELECT COUNT(*) AS n FROM memories")["n"]
        self.info.setText(f"{len(rows)} shown · {total} stored · database {paths.db_path()}")

    def _selected(self) -> tuple[int, str] | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        return int(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)), self.table.item(row, 1).text()

    def _add(self) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "Add memory", "What should I remember?")
        if ok and text.strip():
            kind, ok2 = QInputDialog.getItem(self, "Kind", "Kind", ["preference", "fact", "project", "decision",
                                                                    "routine", "goal", "contact", "note"], 0, False)
            if ok2:
                run_bg(lambda: self.app.memory.add(text, kind=kind, importance=0.8 if kind == "preference" else 0.6),
                       lambda r, e: self.refresh())

    def _edit(self) -> None:
        sel = self._selected()
        if not sel:
            return
        text, ok = QInputDialog.getMultiLineText(self, "Edit memory", "Memory", sel[1])
        if ok and text.strip():
            run_bg(lambda: self.app.memory.update(sel[0], content=text), lambda r, e: self.refresh())

    def _delete(self) -> None:
        sel = self._selected()
        if sel and QMessageBox.question(self, "Delete memory", f"Delete this memory?\n\n{sel[1][:200]}") == \
                QMessageBox.StandardButton.Yes:
            self.app.memory.delete(sel[0])
            self.refresh()

    def _export(self) -> None:
        path = self.app.memory.export()
        self.info.setText(f"exported to {path}")

    def _import(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Import memories", str(paths.backups_dir()), "JSON (*.json)")
        if fn:
            run_bg(lambda: self.app.memory.import_file(Path(fn)),
                   lambda n, e: (self.info.setText(f"imported {n} memories" if not e else f"import failed: {e}"),
                                 self.refresh()))

    def _backup(self) -> None:
        run_bg(self.app.db.backup, lambda p, e: self.info.setText(f"backup written: {p}" if not e else f"backup failed: {e}"))

    def _restore(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Restore database", str(paths.backups_dir()), "Database (*.db)")
        if fn and QMessageBox.question(self, "Restore", "Replace ALL current memory, tasks and schedules with this "
                                       "backup? (A backup of the current state is made first.)") == \
                QMessageBox.StandardButton.Yes:
            self.app.db.backup()
            self.app.db.restore(Path(fn))
            self.refresh()
            self.info.setText(f"restored from {fn}")

    def _reembed(self) -> None:
        run_bg(self.app.memory.retry_embeddings, lambda n, e: self.info.setText(
            f"embedded {n} memories" if not e else f"failed: {e}"))


# ======================================================================= Schedule
class SchedulePanel(QWidget):
    def __init__(self, app: Jarvis) -> None:
        super().__init__()
        self.app = app
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.addWidget(section("Scheduled jobs & reminders"))
        self.jobs = QTableWidget(0, 5)
        self.jobs.setHorizontalHeaderLabels(["Name", "Schedule", "Next run", "Last", "On"])
        self.jobs.verticalHeader().setVisible(False)
        self.jobs.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.jobs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.jobs.setColumnWidth(0, 170)
        self.jobs.setColumnWidth(1, 130)
        self.jobs.setColumnWidth(2, 120)
        lay.addWidget(self.jobs, 2)
        row = QHBoxLayout()
        for text, cb in (("New reminder", self._new_reminder), ("New scheduled task", self._new_task),
                         ("Enable/Disable", self._toggle), ("Edit", self._edit), ("Run now", self._run),
                         ("History", self._history), ("Delete", self._delete)):
            row.addWidget(button(text, cb, "Danger" if text == "Delete" else ""))
        lay.addLayout(row)
        lay.addWidget(section("To-dos"))
        add = QHBoxLayout()
        self.todo_in = QLineEdit()
        self.todo_in.setPlaceholderText("Add a to-do (optionally: '… by friday 5pm')")
        self.todo_in.returnPressed.connect(self._add_todo)
        add.addWidget(self.todo_in)
        add.addWidget(button("Add", self._add_todo))
        lay.addLayout(add)
        self.todos = QListWidget()
        self.todos.itemDoubleClicked.connect(self._toggle_todo)
        lay.addWidget(self.todos, 2)
        hint = QLabel("Double-click a to-do to mark it done/open.")
        hint.setObjectName("Muted")
        lay.addWidget(hint)
        self.refresh()

    def refresh(self) -> None:
        jobs = self.app.scheduler.list()
        self.jobs.setRowCount(len(jobs))
        for i, j in enumerate(jobs):
            name = QTableWidgetItem(j["name"])
            name.setData(Qt.ItemDataRole.UserRole, j["id"])
            self.jobs.setItem(i, 0, name)
            self.jobs.setItem(i, 1, QTableWidgetItem(j["schedule_text"]))
            self.jobs.setItem(i, 2, QTableWidgetItem(j["next_run_text"] if j["enabled"] else "-"))
            last = QTableWidgetItem(j["last_status"] or "")
            last.setForeground(QColor(STATUS_COLOR.get(j["last_status"] or "", theme.DIM)))
            self.jobs.setItem(i, 3, last)
            self.jobs.setItem(i, 4, QTableWidgetItem("●" if j["enabled"] else "○"))
        self.todos.clear()
        now = time.time()
        for t in self.app.db.query("SELECT * FROM todos ORDER BY status DESC, (due IS NULL), due, priority LIMIT 200"):
            due = f"  · due {time.strftime('%a %d %b %H:%M', time.localtime(t['due']))}" if t["due"] else ""
            it = QListWidgetItem(("✓ " if t["status"] == "done" else "○ ") + t["title"] + due)
            it.setData(Qt.ItemDataRole.UserRole, t["id"])
            if t["status"] == "done":
                it.setForeground(QColor(theme.FAINT))
            elif t["due"] and t["due"] < now:
                it.setForeground(QColor(theme.AMBER))
            self.todos.addItem(it)

    def _selected(self) -> int | None:
        r = self.jobs.currentRow()
        return int(self.jobs.item(r, 0).data(Qt.ItemDataRole.UserRole)) if r >= 0 else None

    def _new_reminder(self) -> None:
        msg, ok = QInputDialog.getText(self, "New reminder", "Remind me to…")
        if not ok or not msg.strip():
            return
        when, ok = QInputDialog.getText(self, "When", "When? (e.g. 'in 20 minutes', 'tomorrow 9am')")
        if ok and when.strip():
            from jarvis.services.scheduler import parse_when

            try:
                self.app.scheduler.add(msg[:60], "reminder", {"message": msg}, {"type": "once", "at": parse_when(when)})
            except ValueError as exc:
                QMessageBox.warning(self, "Reminder", str(exc))
            self.refresh()

    def _new_task(self) -> None:
        goal, ok = QInputDialog.getMultiLineText(self, "Scheduled task", "What should JARVIS do?")
        if not ok or not goal.strip():
            return
        spec, ok = QInputDialog.getText(self, "Schedule", "Daily time (HH:MM), or 'every N minutes', or a one-off time:")
        if not ok or not spec.strip():
            return
        import re

        from jarvis.services.scheduler import parse_when

        try:
            if m := re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*", spec):
                sched = {"type": "daily", "time": m.group(1)}
            elif m := re.search(r"every\s+(\d+)\s*min", spec):
                sched = {"type": "interval", "seconds": int(m.group(1)) * 60,
                         "start": time.time() + int(m.group(1)) * 60}
            else:
                sched = {"type": "once", "at": parse_when(spec)}
            self.app.scheduler.add(goal[:60], "agent", {"goal": goal}, sched)
        except ValueError as exc:
            QMessageBox.warning(self, "Schedule", str(exc))
        self.refresh()

    def _toggle(self) -> None:
        jid = self._selected()
        if jid is None:
            return
        job = next(j for j in self.app.scheduler.list() if j["id"] == jid)
        self.app.scheduler.set_enabled(jid, not job["enabled"])
        self.refresh()

    def _edit(self) -> None:
        jid = self._selected()
        if jid is None:
            return
        job = next(j for j in self.app.scheduler.list() if j["id"] == jid)
        text, ok = QInputDialog.getMultiLineText(self, "Edit job (JSON)", "Edit name / payload / schedule:",
                                                 json.dumps({"name": job["name"], "payload": job["payload"],
                                                             "schedule": job["schedule"]}, indent=2))
        if ok:
            try:
                data = json.loads(text)
                self.app.scheduler.update(jid, data.get("name"), data.get("payload"), data.get("schedule"))
            except (ValueError, KeyError) as exc:
                QMessageBox.warning(self, "Edit job", f"Invalid: {exc}")
            self.refresh()

    def _run(self) -> None:
        jid = self._selected()
        if jid is not None:
            self.app.scheduler.run_now(jid)

    def _history(self) -> None:
        jid = self._selected()
        if jid is None:
            return
        runs = self.app.scheduler.runs(jid)
        text = "\n\n".join(f"{time.strftime('%d %b %H:%M:%S', time.localtime(r['started']))}  [{r['status']}]\n"
                           f"{(r['output'] or '')[:400]}" for r in runs) or "No runs yet."
        QMessageBox.information(self, "Run history", text)

    def _delete(self) -> None:
        jid = self._selected()
        if jid is not None and QMessageBox.question(self, "Delete job", "Delete this job?") == \
                QMessageBox.StandardButton.Yes:
            self.app.scheduler.delete(jid)
            self.refresh()

    def _add_todo(self) -> None:
        import re

        from jarvis.services.scheduler import parse_when

        text = self.todo_in.text().strip()
        if not text:
            return
        due = None
        m = re.search(r"\b(?:by|due|on)\s+(.+)$", text, re.I)
        if m:
            try:
                due = parse_when(m.group(1))
                text = text[: m.start()].strip()
            except ValueError:
                due = None
        self.app.db.execute("INSERT INTO todos(title, due, created) VALUES (?,?,?)", (text, due, time.time()))
        self.todo_in.clear()
        self.refresh()

    def _toggle_todo(self, item: QListWidgetItem) -> None:
        tid = item.data(Qt.ItemDataRole.UserRole)
        row = self.app.db.one("SELECT status FROM todos WHERE id=?", (tid,))
        new = "open" if row and row["status"] == "done" else "done"
        self.app.db.execute("UPDATE todos SET status=?, completed=? WHERE id=?",
                            (new, time.time() if new == "done" else None, tid))
        self.refresh()


# ========================================================================= System
class SystemPanel(QWidget):
    def __init__(self, app: Jarvis) -> None:
        super().__init__()
        self.app = app
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.addWidget(section("Performance"))
        self.perf = QLabel("…")
        self.perf.setWordWrap(True)
        self.perf.setStyleSheet(f"font-family: {theme.MONO}; font-size: 11px;")
        lay.addWidget(self.perf)
        row = QHBoxLayout()
        row.addWidget(button("Run self-test", self.self_test, "Primary"))
        row.addWidget(button("Open logs folder", lambda: os.startfile(str(paths.logs_dir()))))  # type: ignore[attr-defined]
        row.addWidget(button("Open data folder", lambda: os.startfile(str(paths.data_home()))))  # type: ignore[attr-defined]
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(section("Self-test"))
        self.results = QTreeWidget()
        self.results.setHeaderLabels(["Check", "Status", "Detail"])
        self.results.setColumnWidth(0, 200)
        self.results.setColumnWidth(1, 80)
        lay.addWidget(self.results, 2)
        lay.addWidget(section("Audit log (recent tool actions)"))
        self.audit = QListWidget()
        lay.addWidget(self.audit, 1)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_perf)
        self._timer.start(2500)
        self._refresh_perf()
        self.refresh_audit()

    def _refresh_perf(self) -> None:
        if not self.isVisible():
            return
        run_bg(self.app.diagnostics.performance, self._show_perf)

    def _show_perf(self, info: dict[str, Any] | None, err: Exception | None) -> None:
        if err or not info:
            self.perf.setText(f"performance unavailable: {err}")
            return
        gpu = info.get("gpu")
        lines = [f"CPU {info['cpu_percent']:>5.1f}%   RAM {info['ram_percent']:>5.1f}%   "
                 f"JARVIS {info['jarvis_rss_mb']} MB / {info['jarvis_threads']} threads",
                 f"Active tasks: {info['active_tasks']}   Loaded models: {', '.join(info['loaded_models']) or 'none'}"]
        if gpu:
            lines.append(f"GPU {gpu['name']}: {gpu['util_pct']}% · VRAM {gpu['vram_used_mb']}/{gpu['vram_total_mb']} MB")
        v = self.app.voice
        if v is not None:
            s = v.status()
            lines.append(f"Mic: {s['input']}  →  Out: {s['output']}  ·  STT {s['stt']} · TTS {s['tts']} · "
                         f"voice ID {s['speaker_id']}")
        self.perf.setText("\n".join(lines))

    def self_test(self) -> None:
        self.results.clear()
        self.results.addTopLevelItem(QTreeWidgetItem(["running…", "", ""]))
        run_bg(self.app.diagnostics.run, self._show_results)

    def _show_results(self, checks: list[Any] | None, err: Exception | None) -> None:
        self.results.clear()
        if err:
            self.results.addTopLevelItem(QTreeWidgetItem(["self-test", "error", str(err)]))
            return
        for c in checks or []:
            it = QTreeWidgetItem([f"{c.area} · {c.name}", c.status, c.detail + (f"  → {c.fix}" if c.fix and
                                                                                 c.status != "ready" else "")])
            it.setForeground(1, QColor(STATUS_COLOR.get(c.status, theme.DIM)))
            it.setToolTip(2, it.text(2))
            self.results.addTopLevelItem(it)

    def refresh_audit(self) -> None:
        self.audit.clear()
        for r in self.app.db.query("SELECT ts, tool, category, decision, ok, summary FROM audit ORDER BY id DESC LIMIT 80"):
            ok = "✓" if r["ok"] == 1 else "✕" if r["ok"] == 0 else "·"
            it = QListWidgetItem(f"{time.strftime('%H:%M:%S', time.localtime(r['ts']))} {ok} {r['tool']} "
                                 f"[{r['category']}/{r['decision']}] {(r['summary'] or '')[:120]}")
            if r["ok"] == 0:
                it.setForeground(QColor(theme.RED))
            self.audit.addItem(it)


# ======================================================================= Settings
EDGE_VOICES = ["en-GB-RyanNeural", "en-GB-ThomasNeural", "en-US-GuyNeural", "en-US-AndrewNeural",
               "en-US-BrianNeural", "en-AU-WilliamNeural", "en-IN-PrabhatNeural", "en-GB-SoniaNeural",
               "en-US-AriaNeural", "en-US-JennyNeural"]


class SettingsPanel(QScrollArea):
    def __init__(self, app: Jarvis) -> None:
        super().__init__()
        self.app = app
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        self.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(4, 8, 10, 8)
        cfg = app.config

        # ---- voice
        lay.addWidget(section("Voice & headset"))
        f = QFormLayout()
        self.mic = QComboBox()
        self.out = QComboBox()
        self._fill_devices()
        f.addRow("Microphone", self.mic)
        f.addRow("Speaker", self.out)
        self.prefer_headset = QCheckBox("Prefer headset devices automatically")
        self.prefer_headset.setChecked(bool(cfg.get("voice.prefer_headset", True)))
        self.avoid_builtin = QCheckBox("Never use the laptop's built-in microphone array")
        self.avoid_builtin.setChecked(bool(cfg.get("voice.avoid_builtin_mic", True)))
        self.wake = QCheckBox("Wake word “Hey JARVIS” (off = always listening)")
        self.wake.setChecked(bool(cfg.get("voice.wake_word", True)))
        self.barge = QCheckBox("Let me interrupt JARVIS by speaking")
        self.barge.setChecked(bool(cfg.get("voice.barge_in", True)))
        for w in (self.prefer_headset, self.avoid_builtin, self.wake, self.barge):
            f.addRow("", w)
        self.tts_voice = QComboBox()
        self.tts_voice.addItems(EDGE_VOICES)
        self.tts_voice.setEditable(True)
        self.tts_voice.setCurrentText(cfg.get("voice.tts_voice", "en-GB-RyanNeural"))
        f.addRow("Voice", self.tts_voice)
        self.rate = QSpinBox()
        self.rate.setRange(-50, 50)
        self.rate.setSuffix(" %")
        self.rate.setValue(int(cfg.get("voice.tts_rate", 0)))
        f.addRow("Speech rate", self.rate)
        self.volume = QSpinBox()
        self.volume.setRange(0, 100)
        self.volume.setSuffix(" %")
        self.volume.setValue(int(cfg.get("voice.tts_volume", 100)))
        f.addRow("Speech volume", self.volume)
        lay.addLayout(f)
        row = QHBoxLayout()
        row.addWidget(button("Refresh devices", self._fill_devices))
        row.addWidget(button("Test speech", self._test_speech))
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(section("Recognise my voice"))
        f2 = QFormLayout()
        self.sid_mode = QComboBox()
        self.sid_mode.addItems(["enforce", "tag", "off"])
        self.sid_mode.setCurrentText(cfg.get("voice.speaker_id.mode", "enforce"))
        self.sid_mode.setToolTip("enforce: only respond to your voice · tag: respond to anyone, show who · off")
        f2.addRow("Mode", self.sid_mode)
        self.sid_status = QLabel("")
        self.sid_status.setWordWrap(True)
        f2.addRow("Status", self.sid_status)
        lay.addLayout(f2)
        row = QHBoxLayout()
        row.addWidget(button("Test microphone", self._mic_test))
        row.addWidget(button("Train wake word", self._train_wake))
        row.addWidget(button("Enrol my voice", self._enroll, "Primary"))
        row.addWidget(button("Forget my voice", self._forget_voice, "Danger"))
        row.addStretch(1)
        lay.addLayout(row)
        self.enroll_prompt = QLabel("")
        self.enroll_prompt.setWordWrap(True)
        self.enroll_prompt.setStyleSheet(f"color: {theme.CYAN}; font-size: 14px; padding: 6px 0;")
        lay.addWidget(self.enroll_prompt)

        # ---- models
        lay.addWidget(section("AI models"))
        self.models_info = QLabel("")
        self.models_info.setWordWrap(True)
        self.models_info.setStyleSheet(f"font-family: {theme.MONO}; font-size: 11px;")
        lay.addWidget(self.models_info)
        f3 = QFormLayout()
        self.override = QComboBox()
        self.override.setEditable(True)
        f3.addRow("Force model", self.override)
        self.anthropic_on = QCheckBox("Enable Anthropic (Claude)")
        self.anthropic_on.setChecked(bool(cfg.get("models.providers.anthropic.enabled")))
        self.anthropic_key = QLineEdit()
        self.anthropic_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.anthropic_key.setPlaceholderText("stored" if secrets.has("ANTHROPIC_API_KEY") else "sk-ant-… (saved to "
                                              "Windows Credential Manager)")
        self.openai_on = QCheckBox("Enable OpenAI-compatible")
        self.openai_on.setChecked(bool(cfg.get("models.providers.openai.enabled")))
        self.openai_url = QLineEdit(cfg.get("models.providers.openai.base_url", ""))
        self.openai_key = QLineEdit()
        self.openai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key.setPlaceholderText("stored" if secrets.has("OPENAI_API_KEY") else "API key")
        f3.addRow("", self.anthropic_on)
        f3.addRow("Anthropic key", self.anthropic_key)
        f3.addRow("", self.openai_on)
        f3.addRow("OpenAI base URL", self.openai_url)
        f3.addRow("OpenAI key", self.openai_key)
        # Free, fast cloud models + email credentials, all stored in Windows Credential Manager
        self.secret_fields: dict[str, QLineEdit] = {}
        for secret, label, hint in (
            ("GROQ_API_KEY", "Groq key (free, fastest)", "gsk_… from console.groq.com/keys"),
            ("GEMINI_API_KEY", "Gemini key (free, vision)", "AIza… from aistudio.google.com/apikey"),
            ("JARVIS_GMAIL_ADDRESS", "Gmail address", "you@gmail.com (for sending/reading email)"),
            ("JARVIS_GMAIL_APP_PASSWORD", "Gmail app password", "16 letters from myaccount.google.com/apppasswords"),
        ):
            edit = QLineEdit()
            if "ADDRESS" not in secret:
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setPlaceholderText("stored ✓" if secrets.has(secret) else hint)
            self.secret_fields[secret] = edit
            f3.addRow(label, edit)
        lay.addLayout(f3)
        row = QHBoxLayout()
        row.addWidget(button("Check models", self._refresh_models))
        row.addWidget(button("Connect Claude", self._connect_claude, "Primary"))
        self.brain = QComboBox()
        self.brain.addItems(["auto", "local"])
        self.brain.setCurrentText(self.app.config.get("agent.brain", "auto"))
        self.brain.setToolTip("auto: complex tasks use Claude Code (your Claude plan) when signed in; "
                              "local: only the models on this laptop")
        self.brain.currentTextChanged.connect(lambda v: (self.app.config.set("agent.brain", v),
                                                         self._refresh_models()))
        row.addWidget(QLabel("Task brain"))
        row.addWidget(self.brain)
        row.addStretch(1)
        lay.addLayout(row)

        # ---- permissions
        lay.addWidget(section("Permissions"))
        f4 = QFormLayout()
        self.perm: dict[str, QComboBox] = {}
        labels = {"read": "Read files / screen", "write": "Create & edit files", "execute": "Run commands & apps",
                  "system": "Change system settings", "external": "External actions (push, send)",
                  "destructive": "Delete / kill / force"}
        for cat, label in labels.items():
            cb = QComboBox()
            cb.addItems(["allow", "ask", "deny"])
            cb.setCurrentText(cfg.get(f"permissions.{cat}", "ask"))
            self.perm[cat] = cb
            f4.addRow(label, cb)
        self.roots = QLineEdit("; ".join(cfg.get("permissions.allowed_roots", ["~"])))
        self.roots.setToolTip("Folders JARVIS may write to (separate with ;)")
        f4.addRow("Writable folders", self.roots)
        lay.addLayout(f4)

        # ---- screen & user
        lay.addWidget(section("Screen & profile"))
        f5 = QFormLayout()
        self.observe = QComboBox()
        self.observe.addItems(["on_demand", "continuous", "off"])
        self.observe.setCurrentText(cfg.get("screen.observe", "on_demand"))
        self.observe.setToolTip("continuous: watches the active window (OCR on change) and alerts on visible errors")
        f5.addRow("Screen awareness", self.observe)
        self.avatar = QComboBox()
        self.avatar.addItems(["female", "male", "orb"])
        self.avatar.setCurrentText(cfg.get("ui.avatar", "female"))
        self.avatar.setToolTip("Holographic humanoid (female/male bust) or the abstract orb (orb applies after restart)")
        f5.addRow("Avatar", self.avatar)
        self.start_mode = QComboBox()
        self.start_mode.addItems(["holo", "compact", "conversation", "activity"])
        self.start_mode.setCurrentText(cfg.get("ui.start_mode", "holo"))
        f5.addRow("Open in", self.start_mode)
        self.user_name = QLineEdit(cfg.get("user.name", ""))
        self.address = QLineEdit(cfg.get("user.address_as", "sir"))
        f5.addRow("Your name", self.user_name)
        f5.addRow("Address you as", self.address)
        self.briefing = QCheckBox("Daily briefing at")
        self.briefing.setChecked(bool(cfg.get("productivity.daily_briefing.enabled")))
        self.briefing_time = QLineEdit(cfg.get("productivity.daily_briefing.time", "08:30"))
        brow = QHBoxLayout()
        brow.addWidget(self.briefing)
        brow.addWidget(self.briefing_time)
        f5.addRow("", brow)
        lay.addLayout(f5)

        save = button("Save settings", self.save, "Primary")
        lay.addWidget(save)
        self.saved = QLabel("")
        self.saved.setObjectName("Muted")
        lay.addWidget(self.saved)
        lay.addStretch(1)
        self.update_voice_status()
        self._refresh_models()
        # Scrolling the page must never change a setting: dropdowns/spinners ignore the wheel unless focused.
        for w in body.findChildren(QComboBox) + body.findChildren(QSpinBox):
            w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            w.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: Any) -> bool:  # noqa: N802 - Qt API
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.Type.Wheel and isinstance(obj, (QComboBox, QSpinBox)) and not obj.hasFocus():
            event.ignore()
            return True
        return super().eventFilter(obj, event)

    # ---------------------------------------------------------------- helpers
    def _fill_devices(self) -> None:
        from jarvis.voice import devices

        cur_in = self.app.config.get("voice.input_device", "")
        cur_out = self.app.config.get("voice.output_device", "")
        self.mic.clear()
        self.out.clear()
        self.mic.addItem("Automatic (prefer headset)", "")
        self.out.addItem("Automatic (prefer headphones)", "")
        try:
            for d in devices.list_devices("input"):
                self.mic.addItem(("🎧 " if devices.is_headset(d.name) else "") + d.name, d.name)
            for d in devices.list_devices("output"):
                self.out.addItem(("🎧 " if devices.is_headset(d.name) else "") + d.name, d.name)
        except Exception as exc:  # noqa: BLE001
            self.mic.addItem(f"(device list failed: {exc})", "")
        for combo, cur in ((self.mic, cur_in), (self.out, cur_out)):
            idx = combo.findData(cur)
            combo.setCurrentIndex(max(0, idx))

    def update_voice_status(self) -> None:
        v = self.app.voice
        if v is None:
            self.sid_status.setText(self.app.voice_error or "voice pipeline starting…")
            return
        s = v.status()
        txt = {"enrolled": f"Enrolled ({v.recognizer.name}); threshold {v.recognizer.threshold:.2f}",
               "not enrolled": "Not enrolled yet - JARVIS will respond to anyone until you enrol.",
               "unavailable": "Speaker model unavailable."}[s["speaker_id"]]
        self.sid_status.setText(txt)

    def _test_speech(self) -> None:
        if self.app.voice is None:
            QMessageBox.information(self, "Speech", "Voice is not running.")
            return
        self.app.voice.say(f"Hello {self.app.config.get('user.name', '')}. This is how I sound through "
                           f"{self.app.voice.output_device.name if self.app.voice.output_device else 'your speakers'}.")

    def _enroll(self) -> None:
        v = self.app.voice
        if v is None:
            QMessageBox.warning(self, "Voice", "The voice pipeline isn't running yet.")
            return
        name = self.app.config.get("user.name", "user") or "user"
        self.enroll_prompt.setText("Get ready… read each phrase aloud when it appears.")
        run_bg(lambda: v.enroll(name), self._enroll_done)

    def on_event(self, topic: str, d: dict[str, Any]) -> None:
        if topic == "voice.enroll":
            self.enroll_prompt.setText(f"({d['step']}/{d['total']})  Say:  “{d['phrase']}”")
        elif topic in ("voice.status", "voice.ready"):
            self.update_voice_status()
        elif topic == "voice.devices_changed":
            QTimer.singleShot(1500, self._fill_devices)

    def _enroll_done(self, res: dict[str, Any] | None, err: Exception | None) -> None:
        if err:
            self.enroll_prompt.setText(f"Enrolment failed: {err}")
        else:
            self.enroll_prompt.setText(f"Voice enrolled from {res['samples']} samples (consistency "
                                       f"{res['consistency']:.2f}). I'll now recognise you in real time.")
        self.update_voice_status()

    def _mic_test(self) -> None:
        v = self.app.voice
        if v is None:
            self.enroll_prompt.setText("Voice isn't running yet.")
            return
        self.enroll_prompt.setText("Speak a normal sentence for 5 seconds…")

        def done(rep: dict[str, Any] | None, err: Exception | None) -> None:
            if err or not rep:
                self.enroll_prompt.setText(f"Mic test failed: {err}")
                return
            self.enroll_prompt.setText(
                ("✓ Microphone OK" if rep["ok"] else "⚠ Needs attention") + f" — {rep['device']}\n"
                f"Heard: “{rep['heard'] or '—'}”  ·  peak {rep['peak']}  ·  "
                f"{'wideband' if rep['wideband'] else 'Bluetooth telephone-band'} audio\n" + "\n".join(rep["notes"]))

        run_bg(lambda: v.mic_check(5.0), done)

    def _train_wake(self) -> None:
        v = self.app.voice
        if v is None:
            self.enroll_prompt.setText("Voice isn't running yet.")
            return
        self.enroll_prompt.setText("Say “Hey JARVIS” each time you're prompted…")

        def done(res: dict[str, Any] | None, err: Exception | None) -> None:
            if err or not res:
                self.enroll_prompt.setText(f"Training failed: {err}")
                return
            self.enroll_prompt.setText(f"Learned how you say it: {', '.join(res['learned']) or '—'} "
                                       f"({res['recognised']}/{res['samples']} recognised)")

        run_bg(lambda: v.train_wake(5), done)

    def _forget_voice(self) -> None:
        if self.app.voice and QMessageBox.question(self, "Forget voice", "Delete your stored voiceprint?") == \
                QMessageBox.StandardButton.Yes:
            self.app.voice.forget_voice()
            self.update_voice_status()

    def _refresh_models(self) -> None:
        def work() -> dict[str, Any]:
            status = self.app.router.status()
            status["_claude"] = self.app.claude.available(max_age=0)
            return status

        run_bg(work, self._show_models)

    def _connect_claude(self) -> None:
        try:
            self.app.claude.login()
            self.saved.setText("A window opened for Claude sign-in. Finish it in the browser, then click Check models.")
        except Exception as exc:  # noqa: BLE001
            self.saved.setText(f"Couldn't start Claude sign-in: {exc}")

    def _show_models(self, status: dict[str, Any] | None, err: Exception | None) -> None:
        if err or not status:
            self.models_info.setText(f"model check failed: {err}")
            return
        claude_ok, claude_why = status.pop("_claude", (False, "unknown"))
        lines = [f"tasks   → {'Claude Code ✓' if claude_ok else 'local agent'} ({claude_why})"]
        options = {""}
        for role, rows in status.items():
            use = next((r["candidate"] for r in rows if r["ok"]), "NONE AVAILABLE")
            lines.append(f"{role:<7} → {use}")
            options.update(r["candidate"] for r in rows)
        self.models_info.setText("\n".join(lines))
        cur = self.app.config.get("models.override", "")
        self.override.clear()
        self.override.addItem("(automatic routing)", "")
        for o in sorted(options - {""}):
            self.override.addItem(o, o)
        idx = self.override.findData(cur)
        self.override.setCurrentIndex(max(0, idx))

    def save(self) -> None:
        cfg = self.app.config
        old_devices = (cfg.get("voice.input_device"), cfg.get("voice.output_device"), cfg.get("voice.prefer_headset"),
                       cfg.get("voice.avoid_builtin_mic"))
        cfg.set("voice.input_device", self.mic.currentData() or "", save=False)
        cfg.set("voice.output_device", self.out.currentData() or "", save=False)
        cfg.set("voice.prefer_headset", self.prefer_headset.isChecked(), save=False)
        cfg.set("voice.avoid_builtin_mic", self.avoid_builtin.isChecked(), save=False)
        cfg.set("voice.wake_word", self.wake.isChecked(), save=False)
        cfg.set("voice.barge_in", self.barge.isChecked(), save=False)
        cfg.set("voice.tts_voice", self.tts_voice.currentText().strip(), save=False)
        cfg.set("voice.tts_rate", self.rate.value(), save=False)
        cfg.set("voice.tts_volume", self.volume.value(), save=False)
        cfg.set("voice.speaker_id.mode", self.sid_mode.currentText(), save=False)
        for cat, cb in self.perm.items():
            cfg.set(f"permissions.{cat}", cb.currentText(), save=False)
        cfg.set("permissions.allowed_roots", [r.strip() for r in self.roots.text().split(";") if r.strip()], save=False)
        cfg.set("screen.observe", self.observe.currentText(), save=False)
        cfg.set("ui.start_mode", self.start_mode.currentText(), save=False)
        cfg.set("user.name", self.user_name.text().strip(), save=False)
        cfg.set("user.address_as", self.address.text().strip() or "sir", save=False)
        cfg.set("productivity.daily_briefing", {"enabled": self.briefing.isChecked(),
                                                "time": self.briefing_time.text().strip() or "08:30"}, save=False)
        cfg.set("models.override", self.override.currentData() or "", save=False)
        cfg.set("models.providers.anthropic.enabled", self.anthropic_on.isChecked(), save=False)
        cfg.set("models.providers.openai.enabled", self.openai_on.isChecked(), save=False)
        cfg.set("models.providers.openai.base_url", self.openai_url.text().strip(), save=False)
        cfg.save()
        cfg.set("ui.avatar", self.avatar.currentText())  # notifies the window to swap the hologram
        msgs = []
        try:
            if self.anthropic_key.text().strip():
                secrets.set("ANTHROPIC_API_KEY", self.anthropic_key.text().strip())
                self.anthropic_key.clear()
                self.anthropic_key.setPlaceholderText("stored")
            if self.openai_key.text().strip():
                secrets.set("OPENAI_API_KEY", self.openai_key.text().strip())
                self.openai_key.clear()
                self.openai_key.setPlaceholderText("stored")
            for secret, edit in self.secret_fields.items():
                if edit.text().strip():
                    secrets.set(secret, edit.text().strip())
                    edit.clear()
                    edit.setPlaceholderText("stored ✓")
        except Exception as exc:  # noqa: BLE001
            msgs.append(f"could not store key: {exc}")
        self.app.router.reload()
        v = self.app.voice
        if v is not None:
            v.speaker.player.volume = self.volume.value() / 100
            if v.speaker.primary is not None:
                v.speaker.primary.voice = self.tts_voice.currentText().strip()
                v.speaker.primary.rate_pct = self.rate.value()
            v.speaker._cache.clear()  # cached phrases used the old voice/rate
            new_devices = (cfg.get("voice.input_device"), cfg.get("voice.output_device"),
                           cfg.get("voice.prefer_headset"), cfg.get("voice.avoid_builtin_mic"))
            if new_devices != old_devices:
                v.reopen()
        if self.briefing.isChecked():
            self.app._ensure_briefing_job()
        self._refresh_models()
        self.saved.setText("Saved " + time.strftime("%H:%M:%S") + "." + (" " + "; ".join(msgs) if msgs else ""))
        if v is not None:
            v._go_passive()
            v._publish_status()
