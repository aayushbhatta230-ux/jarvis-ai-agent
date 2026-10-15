"""End-to-end smoke test against the real local model (skipped when Ollama is not running).

Run explicitly:  python -m pytest tests/v4/test_e2e_smoke.py -m e2e
"""

from __future__ import annotations

import time

import httpx
import pytest


def _ollama_up() -> bool:
    try:
        return httpx.get("http://127.0.0.1:11434/api/version", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not _ollama_up(), reason="Ollama is not running")]


def test_startup_model_memory_tools_shutdown(home, workdir, monkeypatch):
    from jarvis.app import Jarvis
    from jarvis.core import hardware
    from jarvis.core.config import Config

    monkeypatch.setattr(hardware, "adapt", lambda *a, **k: [])
    cfg = Config()
    cfg.set("permissions.allowed_roots", [str(workdir.parent)])
    app = Jarvis(config=cfg)
    try:
        app.start(voice=False)
        # 1. model connectivity, with real capability probing
        cand = app.router.resolve("agent")
        assert "tools" in app.router.providers[cand.provider].capabilities(cand.model)
        res = app.router.chat("fast", [{"role": "user", "content": "Reply with exactly: pong"}], max_tokens=10,
                              temperature=0)
        assert "pong" in res.text.lower()
        # 2. memory storage + retrieval (with embeddings when available)
        app.memory.add("The user's favourite programming language is Rust", kind="preference", importance=0.8)
        assert "Rust" in app.memory.search("which language does the user like best")[0]["content"]
        # 3. real agent tool execution with verification
        target = workdir / "e2e.txt"
        run = app.agent.submit(f"Create the file {target} containing exactly the text E2E OK, then read it back "
                               f"to verify it.")
        row = app.agent.wait(run.id, 400)
        assert row["status"].startswith("completed"), row
        assert target.read_text(encoding="utf-8").strip() == "E2E OK"
        tools = {s["tool"] for s in app.db.query("SELECT tool FROM task_steps WHERE task_id=?", (run.id,))}
        assert "write_file" in tools
        # 4. diagnostics see a healthy core
        report = {c.name: c.status for c in app.diagnostics.run(quick=True)}
        assert report["database"] == "ready" and report["provider ollama"] == "ready"
    finally:
        t = time.time()
        app.shutdown()
        assert time.time() - t < 10  # clean, prompt shutdown
