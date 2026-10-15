"""Claude brain: run complex tasks with Claude Code, using JARVIS's tools.

When the `claude` CLI is installed and signed in (the user's own Claude plan), complex
tasks are handed to it. It drives the computer exclusively through JARVIS's tool server,
so approvals, the audit log, filesystem rules and the activity UI work exactly as for the
local agent. Built-in shell/file-editing tools are disabled; read-only and web tools stay.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis.core import paths
from jarvis.core.cancel import Cancelled, CancelToken

if TYPE_CHECKING:
    from jarvis.app import Jarvis

log = logging.getLogger(__name__)
CREATE_NO_WINDOW = 0x08000000

PERSONA = """You are JARVIS, {user}'s personal AI assistant and friend, running on their Windows laptop.
You act on the real computer ONLY through the mcp__jarvis__* tools (screen, apps, windows, clicking, typing,
files, shell commands, browser, media, email, Instagram, reminders, memory). Rules:
- To SEE the screen call mcp__jarvis__take_screenshot (you receive the image) or read_screen_text.
- Prefer structured tools (launch_app, focus_window, click_on_screen, type_text, play_media, video_control,
  skip_ad, send_email, send_instagram_message) over raw coordinates.
- Sending messages/emails and destructive actions automatically ask the user for approval - just call the tool.
- Verify important actions (look at the screen, re-read the file, check the window) before saying they worked.
- Never claim something happened unless a tool result confirms it.
- Your FINAL message is spoken aloud: 1-3 short, warm, natural sentences saying what you did or found. No markdown.
- Messages from speech recognition may contain mishearings or Hindi/Nepali words; infer what {user} meant."""


class ClaudeBrain:
    def __init__(self, app: Jarvis) -> None:
        self.app = app
        self._status: tuple[float, bool, str] = (0.0, False, "")
        self._server = None

    # ------------------------------------------------------------- status
    def exe(self) -> str | None:
        return shutil.which("claude")

    def available(self, max_age: float = 300.0) -> tuple[bool, str]:
        if self.app.config.get("agent.brain", "auto") == "local":
            return False, "disabled in settings (agent.brain = local)"
        ts, ok, why = self._status
        if time.time() - ts < max_age:
            return ok, why
        exe = self.exe()
        if not exe:
            result = (False, "Claude Code (claude CLI) is not installed")
        else:
            try:
                out = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=20,
                                     creationflags=CREATE_NO_WINDOW, encoding="utf-8", errors="replace")
                data = json.loads(out.stdout or "{}")
                result = (True, f"signed in ({data.get('authMethod', '?')})") if data.get("loggedIn") else \
                    (False, "Claude Code is installed but not signed in - Settings → Connect Claude")
            except Exception as exc:  # noqa: BLE001
                result = (False, f"couldn't check Claude Code: {exc}")
        self._status = (time.time(), *result)
        return result

    def mark_unavailable(self, why: str, minutes: float = 30) -> None:
        self._status = (time.time() + minutes * 60 - 300, False, why)

    def login(self) -> None:
        """Open a console window running `claude auth login` (the user completes sign-in in the browser)."""
        exe = self.exe()
        if not exe:
            raise RuntimeError("Claude Code isn't installed (npm install -g @anthropic-ai/claude-code)")
        subprocess.Popen(["cmd.exe", "/c", "start", "Connect Claude", "cmd.exe", "/k", exe, "auth", "login"])
        self._status = (0.0, False, "")

    # --------------------------------------------------------------- run
    def _tool_server(self) -> Any:
        if self._server is None:
            from jarvis.services.mcp_server import ToolServer

            self._server = ToolServer(self.app)
            self._server.start()
        return self._server

    def run(self, task_id: str, goal: str, history: list[dict[str, Any]], cancel: CancelToken,
            on_step: Any, timeout: float = 900) -> tuple[bool, str]:
        """Run the goal with Claude Code. Returns (ok, final_text)."""
        exe = self.exe()
        if not exe:
            return False, "claude CLI missing"
        server = self._tool_server()
        server.task_id, server.cancel = task_id, cancel
        cfg_path = paths.data_home() / "claude_mcp.json"
        cfg_path.write_text(json.dumps({"mcpServers": {"jarvis": {
            "type": "http", "url": server.url, "headers": {"Authorization": f"Bearer {server.token}"}}}}),
            encoding="utf-8")
        user = self.app.config.get("user.name", "the user")
        convo = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in history[-6:])
        prompt = (f"Recent conversation:\n{convo}\n\n" if convo else "") + f"{user}'s request: {goal}"
        argv = [exe, "-p", prompt, "--output-format", "stream-json", "--verbose",
                "--mcp-config", str(cfg_path), "--strict-mcp-config",
                "--allowedTools", "mcp__jarvis", "Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite",
                "--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit", "Task",
                "--append-system-prompt", PERSONA.format(user=user)]
        model = self.app.config.get("agent.claude_model", "")
        if model:
            argv += ["--model", model]
        proc = subprocess.Popen(argv, cwd=str(Path.home()), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
                                encoding="utf-8", errors="replace", bufsize=1)
        final, ok, error = "", False, ""
        deadline = time.time() + timeout

        def watchdog() -> None:
            while proc.poll() is None:
                if cancel.cancelled or time.time() > deadline:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                                   creationflags=CREATE_NO_WINDOW, check=False)
                    return
                time.sleep(0.3)

        threading.Thread(target=watchdog, daemon=True).start()
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("type")
            if kind == "assistant":
                for block in (ev.get("message") or {}).get("content", []):
                    if block.get("type") == "tool_use":
                        name = str(block.get("name", "")).removeprefix("mcp__jarvis__")
                        on_step("tool_call", name, block.get("input") or {})
                    elif block.get("type") == "text" and block.get("text", "").strip():
                        on_step("thought", None, block["text"])
            elif kind == "result":
                final = str(ev.get("result") or "")
                ok = not ev.get("is_error", False)
                if not ok:
                    error = final
        proc.wait(timeout=10)
        if cancel.cancelled:
            raise Cancelled(cancel.reason or "cancelled")
        if not ok:
            low = (error or final).lower()
            if "not logged in" in low or "login" in low:
                self.mark_unavailable("Claude Code is not signed in")
            elif "limit" in low or "quota" in low or "credit" in low:
                self.mark_unavailable("Claude usage limit reached", minutes=60)
            return False, error or final or "Claude Code failed"
        return True, final.strip()
