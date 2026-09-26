"""Deep System Diagnostics and Hardware Telemetry for JARVIS.

Inspects CPU, RAM, battery, disk, and thermal/power states using psutil
and kernel APIs to deliver concise, conversational health summaries.
"""

from __future__ import annotations

import os
import shutil
import time
from typing import Any
import psutil


def get_system_diagnostics() -> dict[str, Any]:
    """Gather comprehensive system telemetry."""
    # CPU
    cpu_percent = psutil.cpu_percent(interval=0.1)
    cpu_count = psutil.cpu_count(logical=True)
    
    # Memory
    mem = psutil.virtual_memory()
    total_ram_gb = round(mem.total / (1024 ** 3), 1)
    used_ram_gb = round(mem.used / (1024 ** 3), 1)
    free_ram_gb = round(mem.available / (1024 ** 3), 1)
    ram_percent = mem.percent

    # Disk C:
    disk = shutil.disk_usage("C:\\")
    total_disk_gb = round(disk.total / (1024 ** 3), 1)
    free_disk_gb = round(disk.free / (1024 ** 3), 1)
    disk_percent = round((disk.used / disk.total) * 100, 1)

    # Battery
    battery = psutil.sensors_battery()
    battery_percent = int(battery.percent) if battery else 100
    power_plugged = bool(battery.power_plugged) if battery else True
    battery_secs_left = battery.secsleft if battery else -1

    # Top processes by memory
    top_procs: list[dict[str, Any]] = []
    try:
        procs = sorted(
            psutil.process_iter(['name', 'memory_percent', 'cpu_percent']),
            key=lambda p: p.info.get('memory_percent') or 0,
            reverse=True
        )[:4]
        for p in procs:
            top_procs.append({
                "name": p.info.get('name'),
                "memory_percent": round(p.info.get('memory_percent') or 0, 1),
                "cpu_percent": round(p.info.get('cpu_percent') or 0, 1),
            })
    except Exception:
        pass

    return {
        "timestamp": time.time(),
        "cpu": {
            "percent": cpu_percent,
            "logical_cores": cpu_count,
        },
        "memory": {
            "total_gb": total_ram_gb,
            "used_gb": used_ram_gb,
            "free_gb": free_ram_gb,
            "percent": ram_percent,
        },
        "disk": {
            "total_gb": total_disk_gb,
            "free_gb": free_disk_gb,
            "percent": disk_percent,
        },
        "power": {
            "battery_percent": battery_percent,
            "plugged_in": power_plugged,
            "secs_remaining": battery_secs_left,
        },
        "top_processes": top_procs,
    }


def format_diagnostics_speech(diag: dict[str, Any] | None = None) -> str:
    """Format diagnostics into natural, concise JARVIS voice speech."""
    d = diag or get_system_diagnostics()
    cpu_p = d["cpu"]["percent"]
    ram_p = d["memory"]["percent"]
    ram_free = d["memory"]["free_gb"]
    battery_p = d["power"]["battery_percent"]
    plugged = d["power"]["plugged_in"]
    disk_free = d["disk"]["free_gb"]

    power_phrase = "plugged in" if plugged else f"on battery at {battery_p}%"
    if plugged and battery_p < 100:
        power_phrase = f"charging at {battery_p}%"
    elif plugged and battery_p >= 100:
        power_phrase = "fully charged and plugged in"

    return (
        f"Master, CPU is running at {cpu_p}%, RAM load is {ram_p}% with {ram_free} gigabytes available. "
        f"Power status is {power_phrase}, and drive C has {disk_free} gigabytes free."
    )
