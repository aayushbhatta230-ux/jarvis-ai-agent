"""Command execution and managed background processes (dev servers, watchers)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.core.permissions import classify_command
from jarvis.tools.registry import ToolContext, ToolError, ToolResult, tool

CREATE_NO_WINDOW = 0x08000000
_ENC = "utf-8"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["PYTHONUTF8"] = "1"
    return env


def run_process(argv: list[str], cwd: str | None, timeout: float, ctx: ToolContext | None = None,
                stdin: str | None = None) -> tuple[int, str, bool]:
    """Run a process, streaming output; returns (exit_code, output, timed_out).

    The process tree is killed on timeout or cancellation.
    """
    proc = subprocess.Popen(
        argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW, env=_env(),
    )
    out: list[bytes] = []

    def reader() -> None:
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.read(4096), b""):  # type: ignore[union-attr]
            out.append(chunk)

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    if stdin is not None and proc.stdin:
        try:
            proc.stdin.write(stdin.encode(_ENC))
            proc.stdin.close()
        except OSError:
            pass
    deadline = time.time() + timeout
    timed_out = False
    while proc.poll() is None:
        if ctx is not None and ctx.cancel.cancelled:
            _kill_tree(proc.pid)
            break
        if time.time() > deadline:
            timed_out = True
            _kill_tree(proc.pid)
            break
        time.sleep(0.05)
    th.join(timeout=2)
    text = b"".join(out).decode(_ENC, errors="replace")
    return proc.returncode if proc.returncode is not None else -1, text, timed_out


def _kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True,
                   creationflags=CREATE_NO_WINDOW, check=False)


def _cwd(ctx: ToolContext, cwd: str) -> str | None:
    if not cwd:
        return None
    p = ctx.app.permissions.check_path(os.path.expandvars(cwd))
    if not p.is_dir():
        raise ToolError(f"working directory {p} does not exist")
    return str(p)


def _format(code: int, output: str, timed_out: bool, limit: int = 10_000) -> ToolResult:
    if len(output) > limit:
        output = output[: limit // 3] + "\n... [middle truncated] ...\n" + output[-(2 * limit // 3):]
    if timed_out:
        return ToolResult(False, output=output, error="command timed out and was stopped", data={"exit_code": code})
    ok = code == 0
    return ToolResult(ok, output=f"exit code {code}\n{output}".strip(),
                      error="" if ok else f"command exited with code {code}", data={"exit_code": code})


@tool("run_command", "Run a PowerShell command (default) or cmd command and return its output. "
      "Use for builds, tests, git, package installs, diagnostics.",
      {"command": {"type": "string"},
       "cwd": {"type": "string", "description": "Working directory"},
       "shell": {"type": "string", "enum": ["powershell", "cmd"]},
       "timeout": {"type": "integer", "description": "Seconds, default 120, max 900"}},
      required=["command"], category="execute", timeout=920, group="shell",
      escalate=lambda a: classify_command(a.get("command", "")),
      summarize=lambda a: f"run: {a.get('command', '')[:300]}" + (f"  (in {a['cwd']})" if a.get("cwd") else ""))
def run_command(ctx: ToolContext, command: str, cwd: str = "", shell: str = "powershell",
                timeout: int = 120) -> ToolResult:
    timeout = max(5, min(int(timeout), 900))
    if shell == "cmd":
        argv = ["cmd.exe", "/d", "/s", "/c", command]
    else:
        argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command]
    code, out, to = run_process(argv, _cwd(ctx, cwd), timeout, ctx)
    return _format(code, out, to)


@tool("run_python", "Run a Python snippet in a separate process (JARVIS's interpreter) and return stdout/stderr.",
      {"code": {"type": "string"}, "cwd": {"type": "string"}, "timeout": {"type": "integer"}},
      required=["code"], category="execute", timeout=320, group="shell",
      summarize=lambda a: "run python:\n" + a.get("code", "")[:400])
def run_python(ctx: ToolContext, code: str, cwd: str = "", timeout: int = 60) -> ToolResult:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as fh:
        fh.write(code)
        script = fh.name
    try:
        code_, out, to = run_process([sys.executable, script], _cwd(ctx, cwd), max(5, min(timeout, 300)), ctx)
    finally:
        Path(script).unlink(missing_ok=True)
    return _format(code_, out, to)


# --------------------------------------------------------------- background processes
@dataclass
class ManagedProcess:
    id: int
    name: str
    command: str
    cwd: str
    proc: subprocess.Popen[bytes]
    started: float = field(default_factory=time.time)
    log: deque[str] = field(default_factory=lambda: deque(maxlen=2000))

    @property
    def running(self) -> bool:
        return self.proc.poll() is None


class ProcessManager:
    def __init__(self) -> None:
        self._procs: dict[int, ManagedProcess] = {}
        self._next = 1
        self._lock = threading.Lock()

    def start(self, name: str, command: str, cwd: str | None) -> ManagedProcess:
        argv = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]
        proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW, env=_env())
        with self._lock:
            mp = ManagedProcess(self._next, name, command, cwd or os.getcwd(), proc)
            self._procs[mp.id] = mp
            self._next += 1

        def pump() -> None:
            assert proc.stdout is not None
            for raw in iter(proc.stdout.readline, b""):
                mp.log.append(raw.decode(_ENC, errors="replace").rstrip())
            mp.log.append(f"[process exited with code {proc.wait()}]")

        threading.Thread(target=pump, name=f"proc-{mp.id}", daemon=True).start()
        return mp

    def get(self, pid: int) -> ManagedProcess:
        mp = self._procs.get(pid)
        if mp is None:
            raise ToolError(f"no managed process with id {pid}")
        return mp

    def list(self) -> list[dict[str, Any]]:
        return [
            {"id": p.id, "name": p.name, "command": p.command, "cwd": p.cwd, "running": p.running,
             "pid": p.proc.pid, "uptime_s": int(time.time() - p.started)}
            for p in self._procs.values()
        ]

    def stop(self, pid: int) -> None:
        mp = self.get(pid)
        if mp.running:
            _kill_tree(mp.proc.pid)

    def stop_all(self) -> None:
        for mp in list(self._procs.values()):
            if mp.running:
                _kill_tree(mp.proc.pid)


PROCESSES = ProcessManager()


@tool("start_background_process", "Start a long-running command (dev server, watcher) in the background. "
      "Returns an id; use process_output to read its logs and stop_background_process to stop it.",
      {"name": {"type": "string"}, "command": {"type": "string"}, "cwd": {"type": "string"},
       "wait_seconds": {"type": "number", "description": "Seconds to wait before returning initial output"}},
      required=["command"], category="execute", group="shell", timeout=90,
      escalate=lambda a: classify_command(a.get("command", "")))
def start_background_process(ctx: ToolContext, command: str, name: str = "", cwd: str = "",
                             wait_seconds: float = 4) -> ToolResult:
    mp = PROCESSES.start(name or command.split()[0], command, _cwd(ctx, cwd))
    ctx.cancel.wait(max(0.5, min(wait_seconds, 60)))
    tail = "\n".join(list(mp.log)[-40:])
    status = "running" if mp.running else f"exited ({mp.proc.returncode})"
    return ToolResult(mp.running or mp.proc.returncode == 0,
                      output=f"process id {mp.id} [{status}]\n{tail}",
                      error="" if mp.running or mp.proc.returncode == 0 else "process exited early",
                      data={"id": mp.id, "running": mp.running})


@tool("process_output", "Read recent output of a background process started by JARVIS.",
      {"id": {"type": "integer"}, "lines": {"type": "integer"}}, required=["id"], group="shell")
def process_output(ctx: ToolContext, id: int, lines: int = 80) -> str:  # noqa: A002
    mp = PROCESSES.get(id)
    return f"[{'running' if mp.running else 'stopped'}] {mp.command}\n" + "\n".join(list(mp.log)[-lines:])


@tool("list_background_processes", "List background processes started by JARVIS.", group="shell")
def list_background_processes(ctx: ToolContext) -> list[dict[str, Any]]:
    return PROCESSES.list()


@tool("stop_background_process", "Stop a background process started by JARVIS.", {"id": {"type": "integer"}},
      required=["id"], category="execute", group="shell")
def stop_background_process(ctx: ToolContext, id: int) -> str:  # noqa: A002
    PROCESSES.stop(id)
    time.sleep(0.5)
    return f"process {id} {'still running' if PROCESSES.get(id).running else 'stopped'}"
