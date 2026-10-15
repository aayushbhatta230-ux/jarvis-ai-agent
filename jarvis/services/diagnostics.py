"""Self-test and diagnostics: which subsystems are ready, degraded or unavailable."""

from __future__ import annotations

import importlib
import logging
import shutil
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import psutil

from jarvis.core import paths

if TYPE_CHECKING:
    from jarvis.app import Jarvis

log = logging.getLogger(__name__)
READY, DEGRADED, UNAVAILABLE = "ready", "degraded", "unavailable"


@dataclass
class Check:
    area: str
    name: str
    status: str
    detail: str
    fix: str = ""


class Diagnostics:
    def __init__(self, app: Jarvis) -> None:
        self.app = app

    def run(self, quick: bool = False) -> list[Check]:
        checks: list[Check] = []
        for fn in (self._deps, self._storage, self._models, self._audio, self._screen, self._services,
                   self._resources):
            try:
                checks.extend(fn(quick))
            except Exception as exc:  # noqa: BLE001 - a broken check must still be reported
                log.exception("diagnostic %s crashed", fn.__name__)
                checks.append(Check("diagnostics", fn.__name__.strip("_"), UNAVAILABLE, f"check crashed: {exc}"))
        self.app.events.publish("diagnostics.report", checks=[c.__dict__ for c in checks])
        return checks

    @staticmethod
    def format(checks: list[Check]) -> str:
        icon = {READY: "OK ", DEGRADED: "~~ ", UNAVAILABLE: "XX "}
        lines = []
        for c in checks:
            line = f"{icon[c.status]} [{c.area}] {c.name}: {c.detail}"
            if c.fix and c.status != READY:
                line += f"  -> {c.fix}"
            lines.append(line)
        counts = {s: sum(1 for c in checks if c.status == s) for s in (READY, DEGRADED, UNAVAILABLE)}
        lines.append(f"\n{counts[READY]} ready, {counts[DEGRADED]} degraded, {counts[UNAVAILABLE]} unavailable")
        return "\n".join(lines)

    # ------------------------------------------------------------------ checks
    def _deps(self, quick: bool) -> list[Check]:
        mods = {
            "PySide6.QtWidgets": "desktop UI", "numpy": "core", "httpx": "model APIs", "sounddevice": "audio",
            "onnxruntime": "wake word / VAD / voice ID", "edge_tts": "neural voice", "miniaudio": "audio decode",
            "pytesseract": "OCR", "mss": "screen capture", "uiautomation": "UI automation", "pycaw": "volume",
            "bs4": "web extraction", "pypdf": "PDF reading", "docx": "DOCX reading", "send2trash": "safe delete",
            "win32gui": "window control", "win32cred": "credential storage", "psutil": "system info",
        }
        out = []
        missing = []
        for mod, purpose in mods.items():
            try:
                importlib.import_module(mod)
            except Exception as exc:  # noqa: BLE001
                missing.append(f"{mod} ({purpose}): {exc.__class__.__name__}")
        if missing:
            out.append(Check("deps", "python packages", DEGRADED, "; ".join(missing),
                             "pip install -r requirements.txt"))
        else:
            out.append(Check("deps", "python packages", READY, f"{len(mods)} packages import cleanly"))
        try:
            from jarvis.voice._tokenizers_shim import install, install_av_stub

            install_av_stub()
            shim = install()
            importlib.import_module("faster_whisper")
            out.append(Check("deps", "faster-whisper", READY, "importable" + (" (pure-Python tokenizer shim)" if shim else "")))
        except Exception as exc:  # noqa: BLE001
            out.append(Check("deps", "faster-whisper", DEGRADED, f"{exc}", "speech falls back to Google Web Speech"))
        return out

    def _storage(self, quick: bool) -> list[Check]:
        db = self.app.db
        integrity = db.integrity()
        out = [Check("storage", "database", READY if integrity == "ok" else UNAVAILABLE,
                     f"{paths.db_path()} schema v{db.version}, integrity {integrity}, "
                     f"full-text search {'on' if db.fts else 'off'}")]
        n = db.one("SELECT COUNT(*) AS n FROM memories")["n"]
        emb = db.one("SELECT COUNT(*) AS n FROM memories WHERE embedding IS NOT NULL")["n"]
        out.append(Check("storage", "memory", READY if n == 0 or emb == n else DEGRADED,
                         f"{n} memories, {emb} with embeddings",
                         "start Ollama and pull nomic-embed-text, then run 'Re-embed' in Memory"))
        free = shutil.disk_usage(paths.data_home()).free / 1e9
        out.append(Check("storage", "disk", READY if free > 5 else DEGRADED if free > 1 else UNAVAILABLE,
                         f"{free:.1f} GB free on data drive"))
        return out

    def _models(self, quick: bool) -> list[Check]:
        router = self.app.router
        out = []
        for name in router.providers:
            ok, detail = router.provider_ok(name, max_age=0)
            out.append(Check("models", f"provider {name}", READY if ok else UNAVAILABLE, detail,
                             "start Ollama (ollama serve)" if name == "ollama" else "store the API key in Settings"))
        status = router.status()
        for role, rows in status.items():
            usable = [r for r in rows if r["ok"]]
            if usable:
                first = rows[0]
                st = READY if first["ok"] else DEGRADED
                detail = f"using {usable[0]['candidate']}" + ("" if first["ok"] else f" (preferred {first['candidate']}"
                                                                                     f": {first['detail']})")
            else:
                st = UNAVAILABLE
                detail = "; ".join(f"{r['candidate']}: {r['detail']}" for r in rows[:3])
            out.append(Check("models", f"role {role}", st, detail,
                             "ollama pull <model> or enable a cloud provider" if st != READY else ""))
        if not quick and any(r["ok"] for r in status.get("fast", [])):
            t = time.time()
            try:
                res = router.chat("fast", [{"role": "user", "content": "Reply with the single word: ready"}],
                                  max_tokens=10, temperature=0)
                out.append(Check("models", "live inference", READY,
                                 f"{res.provider}:{res.model} answered '{res.text[:20]}' in {time.time() - t:.1f}s"))
            except Exception as exc:  # noqa: BLE001
                out.append(Check("models", "live inference", UNAVAILABLE, str(exc)[:200]))
        return out

    def _audio(self, quick: bool) -> list[Check]:
        v = self.app.voice
        if not self.app.config.get("voice.enabled", True):
            return [Check("voice", "voice", DEGRADED, "disabled in settings (text-only mode)")]
        if v is None:
            err = self.app.voice_error or "still starting"
            return [Check("voice", "voice pipeline", DEGRADED if "starting" in err else UNAVAILABLE, err)]
        s = v.status()
        out = [
            Check("voice", "microphone", READY if s["mic_open"] and s["input_is_headset"] else
                  DEGRADED if s["mic_open"] else UNAVAILABLE,
                  f"{s['input'] or 'none'}" + ("" if s["input_is_headset"] else " (not a headset)") +
                  (f" - error: {s['mic_error']}" if s["mic_error"] else ""),
                  "connect the headset; JARVIS switches automatically"),
            Check("voice", "speech output", READY if s["output"] else UNAVAILABLE, f"{s['output']} via {s['tts']}"),
            Check("voice", "wake word", READY if s["wake_word"] else DEGRADED,
                  "'Hey JARVIS' active" if s["wake_word"] else "off - use Ctrl+Alt+L push-to-talk"),
            Check("voice", "speech recognition", READY if s["stt"] == "whisper" else DEGRADED,
                  f"{s['stt']}" + (" (local)" if s["stt"] == "whisper" else " (online fallback)")),
            Check("voice", "user voice recognition",
                  READY if s["speaker_id"] == "enrolled" else DEGRADED if s["speaker_id"] == "not enrolled" else
                  UNAVAILABLE, f"{s['speaker_id']}, mode {s['speaker_id_mode']}",
                  "Settings -> Voice -> Enrol my voice"),
        ]
        return out

    def _screen(self, quick: bool) -> list[Check]:
        from jarvis.services.screen import _tesseract, grab

        out = []
        try:
            img = grab((0, 0, 64, 64))
            out.append(Check("screen", "capture", READY, f"ok ({img.width}x{img.height} test grab)"))
        except Exception as exc:  # noqa: BLE001
            out.append(Check("screen", "capture", UNAVAILABLE, str(exc)))
        out.append(Check("screen", "OCR", READY if _tesseract() else UNAVAILABLE,
                         "Tesseract ready" if _tesseract() else "Tesseract not found",
                         "install Tesseract OCR (UB Mannheim build) to C:\\Program Files\\Tesseract-OCR"))
        out.append(Check("screen", "observation", READY, f"mode {self.app.config.get('screen.observe')}"))
        return out

    def _services(self, quick: bool) -> list[Check]:
        out = [
            Check("services", "scheduler", READY if self.app.scheduler.alive() else UNAVAILABLE,
                  f"{len(self.app.scheduler.list(False))} active job(s)"),
            Check("services", "tools", READY, f"{len(self.app.tools.tools)} tools registered"),
            Check("services", "agent", READY, f"{len(self.app.agent.active())} active task(s)"),
            Check("services", "Claude brain", READY if self.app.claude.available(max_age=0)[0] else DEGRADED,
                  self.app.claude.available()[1], "Settings → Connect Claude (uses your Claude plan)"),
            Check("services", "external coding agent", READY if shutil.which("claude") else DEGRADED,
                  "claude CLI found" if shutil.which("claude") else "claude CLI not on PATH"),
        ]
        errors = 0
        log_file = paths.logs_dir() / "jarvis.log"
        if log_file.exists():
            cutoff = time.strftime("%Y-%m-%dT%H", time.localtime(time.time() - 3600))
            with open(log_file, encoding="utf-8", errors="ignore") as fh:
                errors = sum(1 for line in fh if '"level": "ERROR"' in line and line[8:21] >= cutoff)
        out.append(Check("services", "recent errors", READY if errors == 0 else DEGRADED,
                         f"{errors} error(s) logged in the last hour", f"see {log_file}"))
        return out

    def _resources(self, quick: bool) -> list[Check]:
        vm = psutil.virtual_memory()
        avail = vm.available / 1e9
        out = [Check("resources", "RAM", READY if avail > 1.5 else DEGRADED,
                     f"{avail:.1f} GB free of {vm.total / 1e9:.1f} GB",
                     "close heavy apps; local models share this memory")]
        from jarvis.tools.system import gpu_info

        gpus = gpu_info()
        if gpus:
            g = gpus[0]
            out.append(Check("resources", "GPU", READY, f"{g['name']}: {g['vram_used_mb']}/{g['vram_total_mb']} MB VRAM"))
        return out

    def performance(self) -> dict[str, Any]:
        me = psutil.Process()
        info: dict[str, Any] = {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "ram_percent": psutil.virtual_memory().percent,
            "jarvis_rss_mb": round(me.memory_info().rss / 1e6),
            "jarvis_threads": me.num_threads(),
            "active_tasks": len(self.app.agent.active()),
            "loaded_models": [],
        }
        ollama = self.app.router.providers.get("ollama")
        if ollama is not None:
            try:
                info["loaded_models"] = [f"{m['name']} ({m.get('size_vram', 0) / 1e9:.1f} GB VRAM)"
                                         for m in ollama.running()]  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        from jarvis.tools.system import gpu_info

        gpus = gpu_info()
        if gpus:
            info["gpu"] = gpus[0]
        return info
