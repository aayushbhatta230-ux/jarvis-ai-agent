"""System-wide hotkeys via the Win32 RegisterHotKey API."""

from __future__ import annotations

import ctypes
import logging
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QByteArray

log = logging.getLogger(__name__)
WM_HOTKEY = 0x0312
MOD = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
VK = {"space": 0x20, "enter": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "f1": 0x70, "f2": 0x71, "f3": 0x72,
      "f4": 0x73, "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
      "`": 0xC0, "pause": 0x13}


def parse(combo: str) -> tuple[int, int]:
    mods, vk = 0, 0
    for part in combo.lower().replace(" ", "").split("+"):
        if part in MOD:
            mods |= MOD[part]
        elif part in VK:
            vk = VK[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            raise ValueError(f"unknown key '{part}' in hotkey '{combo}'")
    if not vk:
        raise ValueError(f"hotkey '{combo}' has no key")
    return mods, vk


class HotkeyManager(QAbstractNativeEventFilter):
    def __init__(self) -> None:
        super().__init__()
        self._handlers: dict[int, Callable[[], None]] = {}
        self._next = 0xB000
        self.failed: list[str] = []

    def register(self, combo: str, handler: Callable[[], None]) -> bool:
        try:
            mods, vk = parse(combo)
        except ValueError as exc:
            log.warning("%s", exc)
            self.failed.append(combo)
            return False
        hid = self._next
        self._next += 1
        if not ctypes.windll.user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
            log.warning("hotkey %s is already used by another application", combo)
            self.failed.append(combo)
            return False
        self._handlers[hid] = handler
        return True

    def unregister_all(self) -> None:
        for hid in list(self._handlers):
            ctypes.windll.user32.UnregisterHotKey(None, hid)
        self._handlers.clear()

    def nativeEventFilter(self, event_type: QByteArray | bytes, message: int) -> tuple[bool, int]:  # noqa: N802
        if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam in self._handlers:
                try:
                    self._handlers[msg.wParam]()
                except Exception:  # noqa: BLE001
                    log.exception("hotkey handler failed")
                return True, 0
        return False, 0
