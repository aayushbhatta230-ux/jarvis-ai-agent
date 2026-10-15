"""Typed, persistent configuration.

Configuration is stored as JSON in ``data/config.json``. Missing keys fall
back to defaults so older config files keep working after upgrades. Secrets
(API keys) are never stored here; see :mod:`jarvis.core.secrets`.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from pathlib import Path
from typing import Any

from jarvis.core import paths

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "user": {
        "name": "Aayush",
        "address_as": "sir",
        "style": "concise, warm, quietly witty; detail only when the task needs it",
    },
    "ui": {
        "start_mode": "holo",  # holo | compact | conversation | activity
        "avatar": "female",  # female | male | orb
        "always_on_top": True,
        "hotkeys": {
            "summon": "ctrl+alt+j",
            "listen": "ctrl+alt+l",
            "command": "ctrl+alt+k",
            "cancel": "ctrl+alt+x",
        },
    },
    "voice": {
        "enabled": True,
        "input_device": "",  # exact or partial device name; "" = auto (prefer headset)
        "output_device": "",  # "" = auto (prefer headphones)
        "prefer_headset": True,
        "avoid_builtin_mic": True,
        "wake_word": True,
        "wake_threshold": 0.5,  # instant trigger
        "wake_confirm_threshold": 0.08,  # borderline scores are confirmed by speech recognition
        "follow_up_seconds": 10.0,  # keep the conversation open after each reply
        "end_silence": 0.6,  # seconds of silence that end an utterance
        "stt_model": "base.en",
        "stt_backend": "google",  # google (accurate on Bluetooth headsets) | whisper (offline)
        "stt_language": "en-IN",
        "wake_check": "cloud",  # cloud: verify short phrases with Google | local: only likely wake phrases
        "tts_backend": "edge",  # edge | sapi
        "tts_voice": "en-GB-RyanNeural",
        "tts_rate": 0,  # percent, -50..+50
        "tts_volume": 100,  # percent 0..100
        "barge_in": True,
        "speaker_id": {
            "mode": "enforce",  # off | tag | enforce
            "threshold": 0.55,
        },
    },
    "models": {
        "providers": {
            "ollama": {"type": "ollama", "base_url": "http://127.0.0.1:11434", "enabled": True},
            "openai": {"type": "openai", "base_url": "https://api.openai.com/v1", "enabled": False,
                        "secret": "OPENAI_API_KEY"},
            "anthropic": {"type": "anthropic", "base_url": "https://api.anthropic.com", "enabled": False,
                           "secret": "ANTHROPIC_API_KEY"},
            # Free tiers (create a key at console.groq.com / aistudio.google.com). Skipped until a key is stored.
            "groq": {"type": "openai", "base_url": "https://api.groq.com/openai/v1", "enabled": True,
                     "secret": "GROQ_API_KEY", "max_tokens_field": "max_tokens", "send_temperature": True,
                     "vision_models": []},
            "gemini": {"type": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                       "enabled": True, "secret": "GEMINI_API_KEY", "max_tokens_field": "max_tokens",
                       "send_temperature": True, "vision_models": ["gemini-flash-latest", "gemini-2.5-flash",
                                                                    "gemini-2.5-flash-lite", "gemini-2.0-flash"]},
        },
        # Each role lists "provider:model" candidates in priority order.
        "roles": {
            "fast": ["groq:llama-3.1-8b-instant", "gemini:gemini-flash-latest", "ollama:qwen3:4b-instruct",
                     "ollama:llama3.2:latest"],
            "chat": ["groq:llama-3.3-70b-versatile", "gemini:gemini-flash-latest", "anthropic:claude-sonnet-5-5",
                     "ollama:qwen3:4b-instruct", "ollama:llama3.2:latest"],
            "agent": ["anthropic:claude-sonnet-5-5", "gemini:gemini-flash-latest", "groq:llama-3.3-70b-versatile",
                      "openai:gpt-5", "ollama:qwen3:4b-instruct", "ollama:llama3.2:latest"],
            "code": ["anthropic:claude-opus-5-5", "anthropic:claude-sonnet-5-5", "gemini:gemini-flash-latest",
                     "groq:llama-3.3-70b-versatile", "openai:gpt-5", "ollama:qwen3:4b-instruct"],
            "vision": ["gemini:gemini-flash-latest", "anthropic:claude-sonnet-5-5", "openai:gpt-5-mini",
                       "ollama:gemma3:4b"],
            "embed": ["ollama:nomic-embed-text:latest"],
        },
        "override": "",  # "provider:model" forces every role to this model
        "request_timeout": 120,
        "keep_alive": "20m",
    },
    "agent": {
        "max_steps": 30,
        "max_retries_per_step": 2,
        "concurrent_tasks": 2,
        "external_coding_agent": "claude",  # claude | none
    },
    "permissions": {
        # allow | ask | deny per category
        "read": "allow",
        "write": "allow",
        "execute": "allow",
        "system": "ask",
        "external": "ask",
        "destructive": "ask",
        "allowed_roots": ["~"],
        "ask_timeout_seconds": 120,
    },
    "screen": {
        "observe": "on_demand",  # off | on_demand | continuous
        "interval_seconds": 4.0,
        "change_threshold": 0.02,
        "tesseract_path": "C:/Program Files/Tesseract-OCR/tesseract.exe",
    },
    "research": {
        "search_provider": "duckduckgo",  # duckduckgo | brave | tavily
    },
    "productivity": {
        "daily_briefing": {"enabled": False, "time": "08:30"},
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    """Thread-safe config with dotted-path access and atomic saves."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.config_path()
        self._lock = threading.RLock()
        self._data = copy.deepcopy(DEFAULTS)
        self._listeners: list[Any] = []
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self.save()
                return
            try:
                user = json.loads(self.path.read_text(encoding="utf-8-sig"))  # tolerate BOM from editors
            except (OSError, json.JSONDecodeError) as exc:
                broken = self.path.with_suffix(".broken.json")
                log.error("config unreadable (%s); preserving copy at %s and using defaults", exc, broken)
                try:
                    self.path.replace(broken)
                except OSError:
                    pass
                user = {}
            self._data = _deep_merge(DEFAULTS, user)

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def get(self, dotted: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self._data
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, dotted: str, value: Any, save: bool = True) -> None:
        with self._lock:
            parts = dotted.split(".")
            node = self._data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
            if save:
                self.save()
        for cb in list(self._listeners):
            try:
                cb(dotted, value)
            except Exception:  # noqa: BLE001 - a listener must not break config writes
                log.exception("config listener failed for %s", dotted)

    def on_change(self, callback: Any) -> None:
        self._listeners.append(callback)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)
