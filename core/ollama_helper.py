"""Ollama process manager to ensure local inference is always online."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
import urllib.request
import urllib.error


def _is_ollama_reachable(host: str = "http://127.0.0.1:11434", timeout: float = 1.5) -> bool:
    """Check if Ollama server responds to /api/tags."""
    url = f"{host.rstrip('/')}/api/tags"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def find_ollama_binary() -> str | None:
    """Find ollama.exe on PATH or known Windows locations."""
    found = shutil.which("ollama")
    if found:
        return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Ollama" / "ollama.exe",
        Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def ensure_ollama_running(max_wait_seconds: float = 12.0) -> bool:
    """Ensure Ollama daemon is running, starting it in the background if necessary."""
    if _is_ollama_reachable():
        print("[OLLAMA] Service is active at http://127.0.0.1:11434.")
        return True

    exe = find_ollama_binary()
    if not exe:
        print("[OLLAMA] WARNING: ollama executable not found on system.")
        return False

    print(f"[OLLAMA] Starting background engine: {exe} serve")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)

    try:
        subprocess.Popen(
            [exe, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
    except Exception as exc:
        print(f"[OLLAMA] Failed to launch ollama serve: {exc}")
        return False

    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        time.sleep(0.6)
        if _is_ollama_reachable():
            print("[OLLAMA] Engine connected and healthy.")
            return True

    print(f"[OLLAMA] WARNING: Engine did not become healthy within {max_wait_seconds}s.")
    return False


if __name__ == "__main__":
    ok = ensure_ollama_running()
    print("Result:", ok)
