"""Filesystem locations used by JARVIS.

Runtime data lives in ``<project>/data`` by default (override with the
``JARVIS_HOME`` environment variable). The project folder is used instead of
%LOCALAPPDATA% because packaged shells on Windows can virtualise AppData,
which would hide models and the database from a normal launch.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def data_home() -> Path:
    override = os.environ.get("JARVIS_HOME", "").strip()
    root = Path(override).expanduser() if override else PROJECT_ROOT / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sub(name: str) -> Path:
    path = data_home() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    return sub("models")


def logs_dir() -> Path:
    return sub("logs")


def backups_dir() -> Path:
    return sub("backups")


def workspace_dir() -> Path:
    """Default folder for research reports, drafts and generated files."""
    return sub("workspace")


def db_path() -> Path:
    return data_home() / "jarvis.db"


def config_path() -> Path:
    return data_home() / "config.json"
