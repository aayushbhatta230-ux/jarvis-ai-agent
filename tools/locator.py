"""
Laptop Location & Hardware Tracking Beacon for JARVIS
======================================================
Retrieves real-time geographic, network, and battery telemetry,
generates interactive maps links, and activates an acoustic sonar
beacon and spoken alert on the laptop speakers to physically locate it.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None


def _sound_acoustic_beacon(iterations: int = 3) -> None:
    """Unmute volume and emit acoustic sonar beeps followed by spoken beacon."""
    def _run():
        try:
            # 1. Unmute and boost volume to ensure audibility
            if pyautogui:
                pyautogui.press("volumemute")  # In case it was muted
                for _ in range(15):
                    pyautogui.press("volumeup")
            
            # 2. Play high-tech sonar beep patterns
            if sys.platform == "win32":
                import winsound
                frequencies = [880, 1175, 1568, 2093]
                for _ in range(iterations):
                    for freq in frequencies:
                        try:
                            winsound.Beep(freq, 120)
                        except Exception:
                            pass
                    time.sleep(0.15)
            
            # 3. Speak out loud through the laptop speakers
            try:
                from voice.speaker import speak
                speak("I am right here, sir! Tracking beacon active on this laptop.")
            except Exception:
                pass
        except Exception as exc:
            print(f"[LOCATOR] Beacon error: {exc}")

    t = threading.Thread(target=_run, name="jarvis-acoustic-beacon", daemon=True)
    t.start()


def locate_laptop(activate_beacon: bool = True) -> dict[str, Any]:
    """Retrieve full location, network, and power telemetry for the laptop.

    Args:
        activate_beacon: If True, rings laptop speakers with sonar beeps & voice.
    """
    hostname = socket.gethostname()
    os_name = f"{platform.system()} {platform.release()}"
    
    # 1. Local IP
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    # 2. Battery status
    battery_info = "Unknown"
    battery_percent = None
    is_charging = False
    if HAS_PSUTIL:
        try:
            batt = psutil.sensors_battery()
            if batt:
                battery_percent = batt.percent
                is_charging = batt.power_plugged
                battery_info = f"{battery_percent}% ({'Plugged in' if is_charging else 'On battery'})"
            else:
                battery_info = "Desktop / AC Power"
        except Exception:
            pass

    # 3. Wi-Fi SSID and Signal
    wifi_ssid = "Unknown"
    wifi_signal = "Unknown"
    if sys.platform == "win32":
        try:
            out = subprocess.check_output("netsh wlan show interfaces", shell=True, text=True, timeout=2)
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("SSID") and not line.startswith("BSSID"):
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        wifi_ssid = parts[1].strip()
                elif line.startswith("Signal"):
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        wifi_signal = parts[1].strip()
        except Exception:
            pass

    # 4. Geolocation via IP
    geo: dict[str, Any] = {}
    maps_url = ""
    city = "Unknown"
    country = "Unknown"
    isp = "Unknown"
    try:
        req = urllib.request.Request(
            "http://ip-api.com/json/",
            headers={"User-Agent": "JARVIS-Assistant/2.0"}
        )
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            geo = json.loads(resp.read().decode("utf-8"))
            city = geo.get("city", "Unknown")
            region = geo.get("regionName", "")
            country = geo.get("country", "Unknown")
            isp = geo.get("isp", "Unknown")
            lat = geo.get("lat")
            lon = geo.get("lon")
            if lat is not None and lon is not None:
                maps_url = f"https://www.google.com/maps?q={lat},{lon}"
    except Exception:
        pass

    # 5. Acoustic Beacon
    if activate_beacon:
        _sound_acoustic_beacon()

    # 6. Spoken and display summaries
    location_str = f"{city}, {country}" if city != "Unknown" else "your current location"
    wifi_str = f"'{wifi_ssid}'" if wifi_ssid != "Unknown" else "local Wi-Fi"
    batt_str = f"battery at {battery_percent}%" if battery_percent is not None else "connected to power"

    spoken = (
        f"I have located your laptop, sir. Connected to Wi-Fi {wifi_str} in {location_str}, with {batt_str}. "
        f"{'Sounding the audio beacon now so you can find it.' if activate_beacon else ''}"
    ).strip()

    markdown = f"""### 📍 Laptop Location & Telemetry
- **Device**: `{hostname}` ({os_name})
- **Status**: {f"🔋 {battery_info}" if battery_percent is not None else "⚡ AC Power"}
- **Network**: Wi-Fi `{wifi_ssid}` (Signal: {wifi_signal})
- **Local IP**: `{local_ip}`
- **Estimated Location**: {city}, {geo.get('regionName', '')} {country} ({isp})
{f"- **Map Coordinates**: [Open in Google Maps]({maps_url})" if maps_url else ""}
- **Acoustic Beacon**: {"🔊 Active (playing sonar alert & speech on laptop speakers)" if activate_beacon else "Inactive"}
"""

    return {
        "ok": True,
        "hostname": hostname,
        "local_ip": local_ip,
        "battery": battery_info,
        "battery_percent": battery_percent,
        "is_charging": is_charging,
        "wifi_ssid": wifi_ssid,
        "wifi_signal": wifi_signal,
        "city": city,
        "country": country,
        "isp": isp,
        "maps_url": maps_url,
        "beacon_active": activate_beacon,
        "spoken": spoken,
        "markdown": markdown,
    }
