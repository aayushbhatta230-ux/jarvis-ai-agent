"""System information, diagnostics and notifications."""

from __future__ import annotations

import platform
import shutil
import subprocess
import time
from typing import Any

import psutil

from jarvis.tools.registry import ToolContext, tool

CREATE_NO_WINDOW = 0x08000000


def gpu_info() -> list[dict[str, Any]]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        r = subprocess.run([exe, "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8,
                           creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 4:
            out.append({"name": parts[0], "vram_total_mb": int(parts[1]), "vram_used_mb": int(parts[2]),
                        "util_pct": int(parts[3])})
    return out


def system_snapshot() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\")
    me = psutil.Process()
    batt = psutil.sensors_battery()
    return {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "cpu": platform.processor(),
        "cpu_cores": psutil.cpu_count(logical=False),
        "cpu_threads": psutil.cpu_count(),
        "cpu_percent": psutil.cpu_percent(interval=0.3),
        "ram_total_gb": round(vm.total / 1e9, 1),
        "ram_used_pct": vm.percent,
        "ram_available_gb": round(vm.available / 1e9, 1),
        "disk_c_free_gb": round(disk.free / 1e9, 1),
        "gpu": gpu_info(),
        "battery": {"percent": batt.percent, "plugged": batt.power_plugged} if batt else None,
        "jarvis_process": {"rss_mb": round(me.memory_info().rss / 1e6), "threads": me.num_threads(),
                           "cpu_percent": me.cpu_percent(interval=0.1)},
        "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 1),
    }


@tool("system_info", "Report CPU, RAM, GPU/VRAM, disk, battery and JARVIS's own resource usage.", group="system")
def system_info(ctx: ToolContext) -> dict[str, Any]:
    return system_snapshot()


@tool("self_test", "Run JARVIS's self-diagnostics and report which subsystems are ready, degraded or unavailable.",
      group="system", timeout=120)
def self_test(ctx: ToolContext) -> str:
    report = ctx.app.diagnostics.run()
    return ctx.app.diagnostics.format(report)


@tool("notify", "Show a desktop notification to the user (and optionally speak it).",
      {"title": {"type": "string"}, "message": {"type": "string"}, "speak": {"type": "boolean"}},
      required=["message"], group="system")
def notify(ctx: ToolContext, message: str, title: str = "JARVIS", speak: bool = False) -> str:
    ctx.app.notify(title, message, speak=speak)
    return "notification shown"


@tool("event_log_errors", "Read recent Error-level entries from the Windows Application event log.",
      {"hours": {"type": "integer"}, "source": {"type": "string"}}, group="system", timeout=60)
def event_log_errors(ctx: ToolContext, hours: int = 24, source: str = "") -> str:
    flt = f"@{{LogName='Application'; Level=2; StartTime=(Get-Date).AddHours(-{max(1, min(hours, 720))})}}"
    cmd = (f"Get-WinEvent -FilterHashtable {flt} -MaxEvents 40 -ErrorAction SilentlyContinue | "
           + (f"Where-Object {{$_.ProviderName -like '*{source}*'}} | " if source else "")
           + "Select-Object TimeCreated, ProviderName, Id, @{n='Message';e={$_.Message.Substring(0, "
             "[Math]::Min(300, $_.Message.Length))}} | Format-List | Out-String -Width 200")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", cmd], capture_output=True, text=True,
                       timeout=50, creationflags=CREATE_NO_WINDOW, encoding="utf-8", errors="replace")
    return r.stdout.strip()[:10000] or "no application errors in that window"
