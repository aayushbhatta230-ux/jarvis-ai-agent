"""Autonomous agent engine: plan -> act -> observe -> verify, with persistence.

Each task runs on a worker thread with its own :class:`CancelToken`. After
every step the conversation, plan and counters are checkpointed to SQLite,
so a task interrupted by a crash or restart can be resumed. Failures are fed
back to the model to drive re-planning; identical failing calls and long
failure streaks are detected and bounded.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from jarvis.agent import prompts
from jarvis.core.cancel import Cancelled, CancelToken
from jarvis.core.events import State
from jarvis.core.logging_setup import redact
from jarvis.providers.base import ToolSpec
from jarvis.providers.router import NoModelAvailable
from jarvis.tools.registry import ToolResult

if TYPE_CHECKING:
    from jarvis.app import Jarvis

log = logging.getLogger(__name__)

TERMINAL = {"completed", "completed_unverified", "failed", "cancelled"}
SIDE_EFFECT_CATEGORIES = {"write", "execute", "system", "external", "destructive"}

GROUP_KEYWORDS: dict[str, tuple[str, ...]] = {
    "files": ("file", "folder", "director", "document", "pdf", "docx", "rename", "move", "copy", "delete", "organi",
              "download", "desktop", "save", "txt", "csv", "path"),
    "shell": ("run ", "command", "powershell", "terminal", "install", "pip ", "npm", "server", "process", "script",
              "python", "execute", "cmd", "dev server", "port"),
    "code": ("code", "repo", "project", "bug", "fix", "test", "build", "lint", "git", "commit", "branch", "function",
             "class ", "refactor", "implement", "feature", "failing", "crash", "stack trace", "compile", "debug"),
    "desktop": ("open", "launch", "close", "window", "click", "type", "press", "volume", "play", "pause", "music",
                "media", "browser", "chrome", "edge", "notepad", "settings", "minimi", "maximi", "switch", "url",
                "website", "clipboard", "lock", "spotify", "youtube", "app"),
    "screen": ("screen", "see", "look at", "visible", "dialog", "button", "showing", "what am i", "this error",
               "this window", "displayed", "on my monitor"),
    "research": ("search", "research", "web", "google", "look up", "find out", "article", "news", "documentation",
                 "compare", "sources", "summari", "report", "latest", "price"),
    "productivity": ("remind", "schedule", "todo", "to-do", "to do", "deadline", "focus", "pomodoro", "briefing",
                     "every day", "daily", "draft", "note", "plan", "agenda", "unfinished", "next step"),
    "live": ("skip", " ad", "video", "pause", "play", "mute", "full screen", "fullscreen", "next", "subtitle",
             "caption", "click", "press the", "button", "rewind", "forward"),
    "comms": ("email", "e-mail", "mail", "inbox", "instagram", "insta", "dm", "message", "text ", "send",
              "reply to", "sorry note", "whatsapp"),
    "memory": ("remember", "forget", "recall", "preference", "last time", "previous", "history"),
    "system": ("cpu", "ram", "memory usage", "gpu", "vram", "disk", "battery", "diagnos", "self test", "self-test",
               "performance", "event log", "health", "notify", "slow"),
}
CORE_TOOLS = {"read_file", "list_directory", "search_files", "run_command", "launch_app", "remember", "recall",
              "screen_context", "web_search", "open_url", "type_text"}


def select_groups(goal: str) -> set[str]:
    g = goal.lower()
    groups = {name for name, kws in GROUP_KEYWORDS.items() if any(k in g for k in kws)}
    if "code" in groups:
        groups |= {"files", "shell"}
    if "comms" in groups:
        groups |= {"screen", "desktop", "memory"}
    if "screen" in groups or "desktop" in groups:
        groups |= {"screen", "desktop"}
    return groups or {"files", "desktop", "screen"}


# Built-in control tools handled by the engine itself (not the registry).
CONTROL_TOOLS = [
    ToolSpec("update_plan", "Create or update the step-by-step plan for this task.", {
        "type": "object",
        "properties": {"steps": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "active", "done", "failed",
                                                                             "skipped"]}}}}},
        "required": ["steps"]}),
    ToolSpec("task_complete", "Finish the task. Report honestly what was done, whether it was verified, and what "
             "remains unresolved.", {
                 "type": "object",
                 "properties": {"summary": {"type": "string", "description": "What was accomplished (for the user)"},
                                "verified": {"type": "boolean", "description": "True only if you checked the outcome"},
                                "evidence": {"type": "string", "description": "How it was verified"},
                                "unresolved": {"type": "string"}},
                 "required": ["summary", "verified"]}),
    ToolSpec("ask_user", "Ask the user a question when you genuinely cannot proceed safely without their input.", {
        "type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}),
    ToolSpec("enable_tools", "Load additional tool groups: files, shell, code, desktop, screen, research, "
             "productivity, memory, system.", {
                 "type": "object", "properties": {"groups": {"type": "array", "items": {"type": "string"}}},
                 "required": ["groups"]}),
]


@dataclass
class TaskRun:
    id: str
    goal: str
    source: str
    cancel: CancelToken
    messages: list[dict[str, Any]] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    groups: set[str] = field(default_factory=set)
    status: str = "queued"
    step: int = 0
    failures_in_row: int = 0
    failed_calls: dict[str, int] = field(default_factory=dict)
    side_effect_pending: bool = False
    done: threading.Event = field(default_factory=threading.Event)
    answer: str | None = None
    answer_event: threading.Event = field(default_factory=threading.Event)
    question: str = ""
    result: str = ""
    model: str = ""


class AgentEngine:
    def __init__(self, app: Jarvis) -> None:
        self.app = app
        self.db = app.db
        self.runs: dict[str, TaskRun] = {}
        workers = max(1, int(app.config.get("agent.concurrent_tasks", 2)))
        self._pool = cf.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="jarvis-agent")
        self._mark_interrupted()

    # --------------------------------------------------------------- lifecycle
    def _mark_interrupted(self) -> None:
        n = self.db.execute("UPDATE tasks SET status='interrupted', updated=? WHERE status IN "
                            "('running','queued','waiting','paused')", (time.time(),)).rowcount
        if n:
            log.info("%d task(s) were interrupted by the last shutdown and can be resumed", n)

    def submit(self, goal: str, source: str = "user", history: list[dict[str, Any]] | None = None,
               project: str = "") -> TaskRun:
        tid = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        run = TaskRun(tid, goal, source, CancelToken(), groups=select_groups(goal))
        now = time.time()
        self.db.execute("INSERT INTO tasks(id, goal, status, created, updated, project, source) VALUES (?,?,?,?,?,?,?)",
                        (tid, goal, "queued", now, now, project, source))
        run.messages = self._initial_messages(goal, history or [], project)
        self.runs[tid] = run
        self._publish(run)
        self._pool.submit(self._run_safe, run)
        return run

    def resume(self, task_id: str) -> TaskRun:
        row = self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not row:
            raise KeyError(task_id)
        if task_id in self.runs and self.runs[task_id].status not in TERMINAL | {"interrupted"}:
            run = self.runs[task_id]
            run.cancel.resume()
            return run
        cp = json.loads(row["checkpoint"] or "{}")
        run = TaskRun(task_id, row["goal"], row["source"], CancelToken(), groups=set(cp.get("groups", [])) or
                      select_groups(row["goal"]))
        run.messages = cp.get("messages") or self._initial_messages(row["goal"], [], row["project"])
        run.plan = cp.get("plan", [])
        run.step = int(cp.get("step", 0))
        run.messages.append({"role": "user", "content": "[JARVIS was restarted or the task was paused. Re-check the "
                             "current state before continuing; earlier actions may or may not have completed.]"})
        self.runs[task_id] = run
        self._pool.submit(self._run_safe, run)
        return run

    def cancel(self, task_id: str | None = None, reason: str = "cancelled by user") -> int:
        targets = [self.runs[task_id]] if task_id and task_id in self.runs else [
            r for r in self.runs.values() if r.status not in TERMINAL]
        for r in targets:
            r.cancel.cancel(reason)
            r.answer_event.set()
        return len(targets)

    def pause(self, task_id: str) -> None:
        run = self.runs[task_id]
        run.cancel.pause()
        self._set_status(run, "paused")

    def unpause(self, task_id: str) -> None:
        run = self.runs[task_id]
        run.cancel.resume()
        self._set_status(run, "running")

    def answer(self, text: str, task_id: str | None = None) -> bool:
        waiting = [r for r in self.runs.values() if r.status == "waiting" and (not task_id or r.id == task_id)]
        if not waiting:
            return False
        run = waiting[-1]
        run.answer = text
        run.answer_event.set()
        return True

    def waiting_question(self) -> TaskRun | None:
        waiting = [r for r in self.runs.values() if r.status == "waiting"]
        return waiting[-1] if waiting else None

    def wait(self, task_id: str, timeout: float = 600) -> dict[str, Any] | None:
        run = self.runs.get(task_id)
        if run:
            run.done.wait(timeout)
        return self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))

    def active(self) -> list[TaskRun]:
        return [r for r in self.runs.values() if r.status not in TERMINAL]

    def shutdown(self) -> None:
        self.cancel(reason="JARVIS shutting down")
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ prompting
    def _initial_messages(self, goal: str, history: list[dict[str, Any]], project: str) -> list[dict[str, Any]]:
        cfg = self.app.config.snapshot()
        memory = self.app.memory.context_block(goal, project=project)
        screen = ""
        if self.app.config.get("screen.observe") == "continuous" and self.app.screen.context.ts:
            screen = self.app.screen.context.summary(1200)
        system = "\n\n".join(filter(None, [
            prompts.identity(cfg),
            prompts.VOICE_INTERPRETATION,
            prompts.AGENT_METHOD,
            prompts.SAFETY,
            prompts.environment_block(self.db.kv_get("last_project")),
            ("Relevant memory:\n" + memory) if memory else "",
            ("Current screen awareness:\n" + screen) if screen else "",
        ]))
        msgs: list[dict[str, Any]] = [{"role": "system", "content": system}]
        if history:
            convo = "\n".join(f"{m['role']}: {m['content'][:400]}" for m in history[-6:])
            msgs.append({"role": "user", "content": f"(Recent conversation for context)\n{convo}"})
            msgs.append({"role": "assistant", "content": "Understood."})
        msgs.append({"role": "user", "content": goal})
        return msgs

    def _tools_for(self, run: TaskRun, local: bool) -> list[ToolSpec]:
        if not local:
            return CONTROL_TOOLS + self.app.tools.specs()
        names = set(CORE_TOOLS)
        specs = self.app.tools.specs(groups=run.groups) + self.app.tools.specs(names=names)
        seen: set[str] = set()
        unique = []
        for s in specs:
            if s.name not in seen:
                seen.add(s.name)
                unique.append(s)
        return CONTROL_TOOLS + unique

    @staticmethod
    def _trim(messages: list[dict[str, Any]], local: bool) -> list[dict[str, Any]]:
        """Keep the context window bounded: elide old tool outputs."""
        limit = 3500 if local else 12000
        keep_recent = 10 if local else 30
        out = []
        n = len(messages)
        for i, m in enumerate(messages):
            m = dict(m)
            if m["role"] == "tool":
                content = m.get("content", "")
                if i < n - keep_recent:
                    m["content"] = content[:300] + ("\n[older output elided]" if len(content) > 300 else "")
                elif len(content) > limit:
                    m["content"] = content[: limit * 2 // 3] + "\n...[truncated]...\n" + content[-limit // 3:]
            if m.get("images") and i < n - 2:
                m.pop("images")
            out.append(m)
        return out

    # ------------------------------------------------------------- execution
    def _run_safe(self, run: TaskRun) -> None:
        try:
            self._run(run)
        except Cancelled as exc:
            self._finish(run, "cancelled", f"Cancelled: {exc}")
        except NoModelAvailable as exc:
            self._finish(run, "failed", "No AI model is available right now.", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - a crashed task must be recorded, not lost
            log.exception("task %s crashed", run.id)
            self._finish(run, "failed", f"The task crashed: {exc.__class__.__name__}: {exc}", error=repr(exc))

    def _run(self, run: TaskRun) -> None:
        max_steps = int(self.app.config.get("agent.max_steps", 30))
        self._set_status(run, "running")
        role = "code" if "code" in run.groups else "agent"
        self.app.events.state(State.CODING if role == "code" else State.PROCESSING, run.goal[:80])
        while run.step < max_steps:
            run.cancel.check()
            cand = self.app.router.resolve(role)
            local = self.app.router.providers[cand.provider].type == "ollama"
            tools = self._tools_for(run, local)
            run.model = cand.key
            result = self.app.router.chat(
                role, self._trim(run.messages, local), tools=tools, cancel=run.cancel,
                on_text=lambda t, tid=run.id: self.app.events.publish("agent.text", task_id=tid, text=t),
                max_tokens=4096 if not local else 1500, temperature=0.2)
            run.step += 1
            if not result.tool_calls and result.text:
                parsed, remainder = parse_text_tool_calls(result.text, {t.name for t in tools})
                if parsed:
                    result.tool_calls, result.text = parsed, remainder
            run.messages.append(result.as_message())
            if result.text:
                self._step(run, "thought", None, None, result.text[:2000], "ok")
            if not result.tool_calls:
                text = result.text.strip()
                if not text:
                    run.messages.append({"role": "user", "content": "Continue: call a tool, or task_complete if done."})
                    continue
                status = "completed_unverified" if run.side_effect_pending else "completed"
                self._finish(run, status, text)
                return
            finished = False
            for call in result.tool_calls:
                run.cancel.check()
                if call.name in ("update_plan", "task_complete", "ask_user", "enable_tools"):
                    finished = self._control(run, call.name, call.arguments, call.id) or finished
                    continue
                self._execute_tool(run, call.name, call.arguments, call.id)
            self._checkpoint(run)
            if finished:
                return
            if run.failures_in_row >= 5:
                self._finish(run, "failed", "I hit five failures in a row and stopped to avoid making things worse. "
                             + self._last_errors(run), error="failure streak")
                return
        self._finish(run, "failed", f"Stopped after {max_steps} steps without finishing. " + self._progress_note(run),
                     error="step limit")

    def _execute_tool(self, run: TaskRun, name: str, args: dict[str, Any], call_id: str) -> None:
        tool = self.app.tools.tools.get(name)
        state = State.CODING if tool and tool.group == "code" else State.SCREEN if tool and tool.group == "screen" \
            else State.WAITING if name in ("wait_for_screen_text", "run_tests", "run_build") else State.EXECUTING
        self.app.events.state(state, f"{name}")
        key = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
        if run.failed_calls.get(key, 0) >= 2:
            res = ToolResult(False, error="this exact call already failed twice; do not repeat it - change approach")
        else:
            res = self.app.tools.execute(name, args, cancel=run.cancel, task_id=run.id)
        if res.ok:
            run.failures_in_row = 0
            if tool and tool.category in SIDE_EFFECT_CATEGORIES:
                run.side_effect_pending = True
            elif tool and tool.category == "read":
                run.side_effect_pending = False  # a later read/inspection counts as a check
        else:
            run.failures_in_row += 1
            run.failed_calls[key] = run.failed_calls.get(key, 0) + 1
            self.app.events.state(State.RECOVERING, f"{name} failed")
        content = res.for_model()
        msg: dict[str, Any] = {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}
        run.messages.append(msg)
        if res.images:
            run.messages.append({"role": "user", "content": f"(Image from {name} attached.)", "images": res.images})
        self._step(run, "tool", name, args, content[:4000], "ok" if res.ok else "error", res.duration)

    def _control(self, run: TaskRun, name: str, args: dict[str, Any], call_id: str) -> bool:
        if name == "update_plan":
            steps = args.get("steps") or []
            run.plan = [{"title": str(s.get("title", s))[:200], "status": s.get("status", "pending")}
                        if isinstance(s, dict) else {"title": str(s)[:200], "status": "pending"} for s in steps][:20]
            self.db.execute("UPDATE tasks SET plan=?, updated=? WHERE id=?", (json.dumps(run.plan), time.time(), run.id))
            self.app.events.publish("agent.plan", task_id=run.id, plan=run.plan)
            self._step(run, "plan", None, None, "\n".join(f"[{s['status']}] {s['title']}" for s in run.plan), "ok")
            run.messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": "plan recorded"})
            return False
        if name == "enable_tools":
            groups = {g for g in (args.get("groups") or []) if g in GROUP_KEYWORDS}
            run.groups |= groups
            run.messages.append({"role": "tool", "tool_call_id": call_id, "name": name,
                                 "content": f"enabled: {', '.join(sorted(run.groups))}"})
            return False
        if name == "ask_user":
            q = str(args.get("question", "")).strip() or "How should I proceed?"
            run.question = q
            run.answer = None
            run.answer_event.clear()
            self._set_status(run, "waiting")
            self.app.events.publish("agent.question", task_id=run.id, question=q)
            self.app.say(q)
            got = run.answer_event.wait(600)
            run.cancel.check()
            self._set_status(run, "running")
            reply = run.answer if got and run.answer else "(no answer within 10 minutes - use your best safe judgement "
            "or stop and report)"
            self._step(run, "question", None, {"question": q}, reply, "ok")
            run.messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": f"User: {reply}"})
            return False
        if name == "task_complete":
            summary = str(args.get("summary", "")).strip() or "Done."
            verified = bool(args.get("verified"))
            unresolved = str(args.get("unresolved", "") or "").strip()
            evidence = str(args.get("evidence", "") or "").strip()
            text = summary
            if unresolved and unresolved.lower() not in ("none", "n/a", "-"):
                text += f"\nUnresolved: {unresolved}"
            status = "completed" if verified else "completed_unverified"
            self._finish(run, status, text, evidence=evidence)
            return True
        return False

    # ---------------------------------------------------------- bookkeeping
    def _step(self, run: TaskRun, kind: str, tool: str | None, args: dict[str, Any] | None, result: str, status: str,
              duration: float | None = None) -> None:
        self.db.execute("INSERT INTO task_steps(task_id, kind, tool, args, result, status, ts, duration) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (run.id, kind, tool, redact(json.dumps(args, default=str))[:4000] if args else None,
                         redact(result)[:8000], status, time.time(), duration))
        self.app.events.publish("agent.step", task_id=run.id, kind=kind, tool=tool, status=status,
                                result=redact(result)[:600], args=redact(json.dumps(args, default=str))[:300] if args else "")

    def _checkpoint(self, run: TaskRun) -> None:
        cp = {"messages": [{k: v for k, v in m.items() if k != "images"} for m in run.messages][-60:],
              "plan": run.plan, "step": run.step, "groups": sorted(run.groups)}
        self.db.execute("UPDATE tasks SET checkpoint=?, updated=?, model=? WHERE id=?",
                        (json.dumps(cp, default=str), time.time(), run.model, run.id))

    def _set_status(self, run: TaskRun, status: str) -> None:
        run.status = status
        self.db.execute("UPDATE tasks SET status=?, updated=? WHERE id=?", (status, time.time(), run.id))
        self._publish(run)
        if status == "paused":
            self.app.events.state(State.PAUSED, run.goal[:80])

    def _publish(self, run: TaskRun) -> None:
        self.app.events.publish("agent.task", task_id=run.id, goal=run.goal, status=run.status, step=run.step,
                                source=run.source, model=run.model, plan=run.plan)

    def _finish(self, run: TaskRun, status: str, result: str, error: str = "", evidence: str = "") -> None:
        run.result = result
        run.status = status
        self._checkpoint(run)
        self.db.execute("UPDATE tasks SET status=?, result=?, error=?, updated=? WHERE id=?",
                        (status, result, error or None, time.time(), run.id))
        self._step(run, "result", None, {"evidence": evidence} if evidence else None, result, status)
        self._publish(run)
        self.app.events.publish("agent.done", task_id=run.id, status=status, result=result, goal=run.goal,
                                source=run.source)
        if status in ("completed", "completed_unverified") and run.step > 2:
            try:
                self.app.memory.add(f"Task '{run.goal[:160]}' -> {status}: {result[:300]}", kind="task_outcome",
                                    importance=0.3, source="agent")
            except Exception:  # noqa: BLE001
                log.debug("could not store task outcome", exc_info=True)
        self.app.events.state(State.COMPLETED if status.startswith("completed") else
                              State.IDLE if status == "cancelled" else State.RECOVERING, status)
        run.done.set()

    def _last_errors(self, run: TaskRun) -> str:
        errs = [m["content"][:200] for m in run.messages if m["role"] == "tool" and m["content"].startswith("ERROR")]
        return "Last errors: " + " | ".join(errs[-3:]) if errs else ""

    def _progress_note(self, run: TaskRun) -> str:
        done = [s["title"] for s in run.plan if s.get("status") == "done"]
        todo = [s["title"] for s in run.plan if s.get("status") not in ("done", "skipped")]
        note = ""
        if done:
            note += "Done: " + "; ".join(done[:5]) + ". "
        if todo:
            note += "Remaining: " + "; ".join(todo[:5]) + ". "
        return note + "You can say 'resume' to continue."


_TOOL_CALL_TAG = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_JSON_CALL = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```|^\s*(\{\s*\"name\".*\})\s*$", re.S)


def parse_text_tool_calls(text: str, known: set[str]) -> tuple[list[Any], str]:
    """Recover tool calls that a model wrote as text instead of using native tool calling."""
    from jarvis.providers.base import ToolCall, parse_arguments

    blobs = [m.group(1) for m in _TOOL_CALL_TAG.finditer(text)]
    if not blobs:
        m = _JSON_CALL.search(text)
        if m:
            blobs = [m.group(1) or m.group(2)]
    calls = []
    for blob in blobs:
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        name = data.get("name") or data.get("tool")
        if name in known:
            calls.append(ToolCall(name=name, arguments=parse_arguments(data.get("arguments") or data.get("parameters")
                                                                        or {})))
    if not calls:
        return [], text
    remainder = _JSON_CALL.sub("", _TOOL_CALL_TAG.sub("", text)).strip()
    return calls, remainder


def spoken_summary(text: str, max_sentences: int = 2) -> str:
    """Condense a result for speech: first sentences, no markdown/code."""
    text = re.sub(r"```.*?```", " (code shown on screen) ", text, flags=re.S)
    text = re.sub(r"[#*_`>|]", "", text)
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[A-Za-z]:\\[^\s,;]+", "the file path shown", text)
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    out = " ".join(sentences[:max_sentences]).strip()
    return out[:400]
