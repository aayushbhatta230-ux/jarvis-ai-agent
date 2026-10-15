"""Audio device discovery, headset-first selection and hot-plug detection.

Selection prefers a headset / headphone microphone (Bluetooth hands-free,
USB or wired jack) and avoids the laptop's built-in microphone array, which
on this machine is unreliable. An explicit device name in config always
wins. Windows endpoint changes (plugging in or connecting a headset) are
detected through the Core Audio API so capture can switch automatically.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass

import sounddevice as sd

log = logging.getLogger(__name__)

_HEADSET = re.compile(
    r"(?i)headset|headphone|hands-?free|earphone|earbud|buds|airpods|bluetooth|\bbt\b|jabra|hyperx|steelseries|"
    r"razer|corsair|logitech|plantronics|poly|sennheiser|bose|jbl|sony\s*wh|sony\s*wf|boat|realme|oneplus|"
    r"usb audio|usb mic|yeti|blue snowball|rode|fifine|maono|external mic|ys-\d+"
)
_BUILTIN_MIC = re.compile(r"(?i)microphone array|smart sound|internal|built-?in|digital microphones")
_EXCLUDE = re.compile(r"(?i)stereo mix|sound mapper|primary sound|what u hear|loopback|wave out|cable output|"
                      r"pc speaker|@system32")
_SPEAKER = re.compile(r"(?i)speaker")


@dataclass
class Device:
    index: int
    name: str
    hostapi: str
    channels: int
    samplerate: int
    score: int = 0


def _hostapi_names() -> list[str]:
    return [h["name"] for h in sd.query_hostapis()]


def list_devices(kind: str) -> list[Device]:
    """Unique input or output devices, preferring WASAPI entries (full names)."""
    apis = _hostapi_names()
    order = {"Windows WASAPI": 0, "MME": 1, "Windows DirectSound": 2, "Windows WDM-KS": 9}
    best: dict[str, Device] = {}
    for idx, d in enumerate(sd.query_devices()):
        ch = d["max_input_channels"] if kind == "input" else d["max_output_channels"]
        if ch <= 0:
            continue
        name = d["name"].strip()
        api = apis[d["hostapi"]]
        if _EXCLUDE.search(name) or api == "Windows WDM-KS":
            continue
        key = re.sub(r"\s+", " ", name.lower())[:31]  # MME truncates names to 31 chars
        dev = Device(idx, name, api, ch, int(d["default_samplerate"]))
        cur = best.get(key)
        if cur is None or order.get(api, 5) < order.get(cur.hostapi, 5):
            best[key] = dev
    return list(best.values())


def score_input(name: str, prefer_headset: bool = True, avoid_builtin: bool = True) -> int:
    s = 0
    if prefer_headset and _HEADSET.search(name):
        s += 100
    if re.search(r"(?i)mic input|line in|external", name):
        s += 40  # wired jack (combo headset port)
    if re.search(r"(?i)web ?cam|camera", name):
        s -= 20
    if _BUILTIN_MIC.search(name):
        s -= 100 if avoid_builtin else 10
    return s


def score_output(name: str, prefer_headset: bool = True) -> int:
    s = 0
    if prefer_headset and _HEADSET.search(name):
        s += 100
        if re.search(r"(?i)headphones", name):
            s += 10  # A2DP/unified endpoint sounds better than the hands-free one
    if _SPEAKER.search(name):
        s -= 10
    if re.search(r"(?i)nvidia|hdmi|display audio", name):
        s -= 50
    return s


def _match(devs: list[Device], wanted: str) -> Device | None:
    w = wanted.lower().strip()
    for d in devs:
        if d.name.lower() == w:
            return d
    for d in devs:
        if w in d.name.lower():
            return d
    return None


def choose_input(configured: str = "", prefer_headset: bool = True, avoid_builtin: bool = True) -> Device | None:
    devs = list_devices("input")
    if configured:
        hit = _match(devs, configured)
        if hit:
            return hit
        log.warning("configured microphone '%s' not found; choosing automatically", configured)
    for d in devs:
        d.score = score_input(d.name, prefer_headset, avoid_builtin)
    devs.sort(key=lambda d: -d.score)
    return devs[0] if devs else None


def choose_output(configured: str = "", prefer_headset: bool = True) -> Device | None:
    devs = list_devices("output")
    if configured:
        hit = _match(devs, configured)
        if hit:
            return hit
        log.warning("configured speaker '%s' not found; choosing automatically", configured)
    for d in devs:
        d.score = score_output(d.name, prefer_headset)
    devs.sort(key=lambda d: -d.score)
    return devs[0] if devs else None


def is_headset(name: str) -> bool:
    return bool(_HEADSET.search(name or ""))


def refresh_portaudio() -> None:
    """Re-enumerate devices (PortAudio caches the list at initialisation).

    Only call while no stream is open.
    """
    try:
        sd._terminate()  # type: ignore[attr-defined]
    finally:
        sd._initialize()  # type: ignore[attr-defined]


def active_endpoints() -> frozenset[str]:
    """Names of currently active Windows audio endpoints (Core Audio API)."""
    import comtypes
    from pycaw.pycaw import AudioUtilities
    from pycaw.utils import AudioDeviceState

    comtypes.CoInitialize()
    try:
        return frozenset(d.FriendlyName for d in AudioUtilities.GetAllDevices()
                         if d.state == AudioDeviceState.Active and d.FriendlyName)
    finally:
        comtypes.CoUninitialize()


class HotplugWatcher:
    """Polls Windows endpoints and calls ``on_change`` when the set changes."""

    def __init__(self, on_change: Callable[[frozenset[str], frozenset[str]], None], interval: float = 2.5) -> None:
        self.on_change = on_change
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="jarvis-audio-hotplug", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        try:
            last = active_endpoints()
        except Exception:  # noqa: BLE001
            log.warning("audio hot-plug detection unavailable", exc_info=True)
            return
        while not self._stop.wait(self.interval):
            try:
                now = active_endpoints()
            except Exception:  # noqa: BLE001
                continue
            if now != last:
                added, removed = now - last, last - now
                log.info("audio devices changed: +%s -%s", sorted(added), sorted(removed))
                last = now
                try:
                    self.on_change(added, removed)
                except Exception:  # noqa: BLE001
                    log.exception("hot-plug handler failed")
