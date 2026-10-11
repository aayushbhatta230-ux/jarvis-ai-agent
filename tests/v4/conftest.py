"""Shared fixtures: isolated data folder and a scripted fake model provider."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.v4.fakes import FakeProvider, reply  # noqa: F401 - re-exported for tests


@pytest.fixture(autouse=True)
def _reset_fake() -> Iterator[None]:
    FakeProvider.scripts = {}
    FakeProvider.calls = []
    FakeProvider.caps = {"m": {"completion", "tools"}, "v": {"completion", "vision"}}
    FakeProvider.healthy = {}
    yield


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture
def config(home: Path):
    from jarvis.core.config import Config
    from jarvis.providers import router

    router.PROVIDER_TYPES["fake"] = FakeProvider
    cfg = Config()
    cfg.set("models.providers", {"fake": {"type": "fake", "enabled": True},
                                 "fake2": {"type": "fake", "enabled": True}}, save=False)
    cfg.set("models.roles", {r: ["fake:m"] for r in ("fast", "chat", "agent", "code")} |
            {"vision": ["fake:v"], "embed": ["fake:e"]}, save=False)
    cfg.set("permissions.allowed_roots", [str(home.parent)], save=False)
    cfg.set("permissions.ask_timeout_seconds", 2, save=False)
    cfg.set("agent.concurrent_tasks", 2, save=False)
    cfg.save()
    return cfg


@pytest.fixture
def app(config, home: Path, monkeypatch: pytest.MonkeyPatch):
    from jarvis.app import Jarvis
    from jarvis.core import hardware

    monkeypatch.setattr(hardware, "adapt", lambda *a, **k: [])
    a = Jarvis(config=config)
    a._warm_up = lambda: None  # don't let warm-up consume scripted replies
    yield a
    a.shutdown()


@pytest.fixture
def workdir(home: Path) -> Path:
    d = home.parent / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
