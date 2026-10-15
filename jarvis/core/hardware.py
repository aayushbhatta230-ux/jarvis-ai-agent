"""Adapt defaults to the machine JARVIS runs on (applied once, on first start)."""

from __future__ import annotations

import logging

import psutil

from jarvis.core.config import Config

log = logging.getLogger(__name__)


def profile() -> dict[str, float]:
    from jarvis.tools.system import gpu_info

    gpus = gpu_info()
    return {
        "ram_gb": psutil.virtual_memory().total / 1e9,
        "cores": float(psutil.cpu_count(logical=False) or 2),
        "vram_mb": float(gpus[0]["vram_total_mb"]) if gpus else 0.0,
    }


def adapt(config: Config, db: object) -> list[str]:
    """Tune concurrency and model sizes to the hardware. Returns the decisions made."""
    if db.kv_get("hardware_adapted"):  # type: ignore[attr-defined]
        return []
    hw = profile()
    notes = [f"hardware: {hw['ram_gb']:.1f} GB RAM, {hw['cores']:.0f} cores, {hw['vram_mb']:.0f} MB VRAM"]
    if hw["ram_gb"] < 12:
        config.set("agent.concurrent_tasks", 1, save=False)
        config.set("screen.interval_seconds", 6.0, save=False)
        config.set("voice.stt_model", "base.en", save=False)
        notes.append("low RAM: 1 concurrent agent task, slower screen polling, Whisper base.en")
    elif hw["ram_gb"] >= 24 and hw["cores"] >= 8:
        config.set("voice.stt_model", "small.en", save=False)
        notes.append("ample RAM: Whisper small.en for better accuracy")
    if hw["vram_mb"] and hw["vram_mb"] < 4000:
        config.set("models.roles.vision", ["anthropic:claude-sonnet-5-5", "openai:gpt-5-mini", "ollama:gemma3:4b"],
                   save=False)
        notes.append("little VRAM: prefer cloud vision when configured")
    config.save()
    db.kv_set("hardware_adapted", True)  # type: ignore[attr-defined]
    for n in notes:
        log.info(n)
    return notes
