"""Windows desktop automation: applications, windows, UI elements, input, media.

Structured interfaces are preferred: window handles via Win32, controls via
UI Automation (accessibility tree). Coordinate clicks are only used when a
control has been located at runtime (UIA bounding box or OCR match).
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Any

import psutil

from jarvis.tools.registry import ToolContext, ToolError, ToolResult, tool

log = logging.getLogger(__name__)

try:
    import win32api
    import win32con
    import win32gui
    import win32process
except Exception:  # pragma: no cover
    win32gui = None  # type: ignore[assignment]

CREATE_NO_WINDOW = 0x08000000

# ----------------------------------------------------------------- windows
def _window_info(hwnd: int) -> dict[str, Any]:
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    try:
        pname = psutil.Process(pid).name()
    except psutil.Error:
        pname = "?"
    rect = win32gui.GetWindowRect(hwnd)
    return {"hwnd": hwnd, "title": win32gui.GetWindowText(hwnd), "process": pname, "pid": pid,
            "rect": rect, "minimized": bool(win32gui.IsIconic(hwnd))}


def list_top_windows() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def cb(hwnd: int, _: Any) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title or title in ("Program Manager", "Windows Input Experience"):
            return True
        if win32gui.GetWindow(hwnd, win32con.GW_OWNER):
            return True
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if ex & win32con.WS_EX_TOOLWINDOW:
            return True
        out.append(_window_info(hwnd))
        return True

    win32gui.EnumWindows(cb, None)
    return out


def active_window() -> dict[str, Any] | None:
    hwnd = win32gui.GetForegroundWindow()
    return _window_info(hwnd) if hwnd else None


def find_window(query: str) -> dict[str, Any] | None:
    q = query.lower().strip()
    wins = list_top_windows()
    for w in wins:  # exact process name first
        if w["process"].lower().removesuffix(".exe") == q.removesuffix(".exe"):
            return w
    for w in wins:
        if q in w["title"].lower() or q in w["process"].lower():
            return w
    return None


def focus_hwnd(hwnd: int) -> bool:
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001 - Windows foreground lock; use the Alt-key workaround
        user32 = ctypes.windll.user32
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001
            fg = win32gui.GetForegroundWindow()
            cur = win32api.GetCurrentThreadId()
            tgt, _ = win32process.GetWindowThreadProcessId(hwnd)
            fgt, _ = win32process.GetWindowThreadProcessId(fg)
            user32.AttachThreadInput(cur, fgt, True)
            user32.AttachThreadInput(cur, tgt, True)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(cur, fgt, False)
            user32.AttachThreadInput(cur, tgt, False)
    time.sleep(0.15)
    return win32gui.GetForegroundWindow() == hwnd


@tool("list_windows", "List open application windows (title, process, handle).", group="desktop")
def list_windows(ctx: ToolContext) -> list[dict[str, Any]]:
    return [{k: w[k] for k in ("hwnd", "title", "process", "minimized")} for w in list_top_windows()]


@tool("focus_window", "Bring a window to the foreground by title text or process name.",
      {"query": {"type": "string"}}, required=["query"], category="execute", group="desktop")
def focus_window(ctx: ToolContext, query: str) -> ToolResult:
    w = find_window(query)
    if not w:
        raise ToolError(f"no open window matches '{query}'")
    ok = focus_hwnd(w["hwnd"])
    return ToolResult(ok, output=f"focused '{w['title']}' ({w['process']})" if ok else "",
                      error="" if ok else f"Windows refused to focus '{w['title']}'", data=w)


@tool("close_window", "Politely close a window (the app may prompt to save).", {"query": {"type": "string"}},
      required=["query"], category="execute", group="desktop")
def close_window(ctx: ToolContext, query: str) -> ToolResult:
    w = find_window(query)
    if not w:
        raise ToolError(f"no open window matches '{query}'")
    win32gui.PostMessage(w["hwnd"], win32con.WM_CLOSE, 0, 0)
    for _ in range(20):
        time.sleep(0.15)
        if not win32gui.IsWindow(w["hwnd"]) or not win32gui.IsWindowVisible(w["hwnd"]):
            return ToolResult(True, output=f"closed '{w['title']}'")
    return ToolResult(False, error=f"'{w['title']}' is still open (it may be showing a save prompt)")


@tool("window_action", "Minimize, maximize or restore a window.",
      {"query": {"type": "string"}, "action": {"type": "string", "enum": ["minimize", "maximize", "restore"]}},
      required=["query", "action"], category="execute", group="desktop")
def window_action(ctx: ToolContext, query: str, action: str) -> str:
    w = find_window(query)
    if not w:
        raise ToolError(f"no open window matches '{query}'")
    flag = {"minimize": win32con.SW_MINIMIZE, "maximize": win32con.SW_MAXIMIZE,
            "restore": win32con.SW_RESTORE}[action]
    win32gui.ShowWindow(w["hwnd"], flag)
    return f"{action}d '{w['title']}'"


# ------------------------------------------------------------- applications
_ALIASES = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe", "paint": "mspaint.exe",
    "explorer": "explorer.exe", "file explorer": "explorer.exe", "files": "explorer.exe",
    "cmd": "cmd.exe", "command prompt": "cmd.exe", "powershell": "powershell.exe",
    "terminal": "wt.exe", "windows terminal": "wt.exe", "task manager": "taskmgr.exe",
    "settings": "ms-settings:", "control panel": "control.exe", "snipping tool": "snippingtool.exe",
    "vscode": "code", "vs code": "code", "visual studio code": "code",
    "chrome": "chrome.exe", "google chrome": "chrome.exe", "edge": "msedge.exe", "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe", "word": "winword.exe", "excel": "excel.exe", "powerpoint": "powerpnt.exe",
    "outlook": "outlook.exe",
}
_start_apps_cache: tuple[float, list[dict[str, str]]] = (0.0, [])


def start_apps() -> list[dict[str, str]]:
    """Installed Start-menu apps (desktop + Store) with their AppIDs."""
    global _start_apps_cache
    ts, cached = _start_apps_cache
    if cached and time.time() - ts < 600:
        return cached
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"],
        capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW, encoding="utf-8",
        errors="replace",
    )
    try:
        data = json.loads(r.stdout or "[]")
        apps = data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        apps = []
    _start_apps_cache = (time.time(), apps)
    return apps


def _match_start_app(name: str) -> dict[str, str] | None:
    q = name.lower().strip()
    apps = start_apps()
    for a in apps:
        if a.get("Name", "").lower() == q:
            return a
    scored = []
    for a in apps:
        n = a.get("Name", "").lower()
        if q in n:
            scored.append((len(n) - len(q), a))
        elif all(tok in n for tok in q.split()):
            scored.append((50 + len(n), a))
    return min(scored, key=lambda s: s[0])[1] if scored else None


def _app_paths(exe: str) -> str | None:
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                return str(winreg.QueryValue(k, None))
        except OSError:
            continue
    return None


@tool("launch_app", "Open an application by name (e.g. 'notepad', 'chrome', 'spotify', 'vs code'), "
      "optionally with arguments. Verifies that a window appears.",
      {"name": {"type": "string"}, "args": {"type": "string"}},
      required=["name"], category="execute", group="desktop", timeout=45)
def launch_app(ctx: ToolContext, name: str, args: str = "") -> ToolResult:
    import shutil

    before = {w["hwnd"] for w in list_top_windows()}
    key = name.lower().strip()
    target = _ALIASES.get(key, name)
    launched = ""
    if target.endswith(":"):
        os.startfile(target)  # type: ignore[attr-defined]
        launched = target
    else:
        exe = target if target.lower().endswith(".exe") else f"{target}.exe"
        path = shutil.which(target) or shutil.which(exe) or _app_paths(exe)
        if path:
            subprocess.Popen([path, *([args] if args else [])], creationflags=0, close_fds=True)
            launched = path
        else:
            app = _match_start_app(name)
            if app:
                subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['AppID']}"])
                launched = f"{app['Name']} ({app['AppID']})"
    if not launched:
        raise ToolError(f"could not find an installed application called '{name}'")
    # Verify: wait for a new top-level window (or an existing one coming to front)
    for _ in range(40):
        if ctx.cancel.wait(0.25):
            break
        wins = list_top_windows()
        new = [w for w in wins if w["hwnd"] not in before]
        if new:
            return ToolResult(True, output=f"launched {launched}; new window '{new[0]['title']}' ({new[0]['process']})",
                              data=new[0])
    fg = active_window()
    return ToolResult(True, output=f"launched {launched}; no new window detected within 10s "
                      f"(it may reuse an existing window). Foreground: {fg['title'] if fg else '?'}",
                      data={"verified": False})


@tool("list_processes", "List running processes sorted by memory or CPU.",
      {"sort": {"type": "string", "enum": ["memory", "cpu"]}, "limit": {"type": "integer"},
       "name": {"type": "string", "description": "Optional filter"}}, group="desktop")
def list_processes(ctx: ToolContext, sort: str = "memory", limit: int = 25, name: str = "") -> list[dict[str, Any]]:
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
        info = p.info
        if name and name.lower() not in (info["name"] or "").lower():
            continue
        procs.append({"pid": info["pid"], "name": info["name"],
                      "memory_mb": round((info["memory_info"].rss if info["memory_info"] else 0) / 1e6, 1),
                      "cpu": info["cpu_percent"]})
    key = "memory_mb" if sort == "memory" else "cpu"
    return sorted(procs, key=lambda x: x[key] or 0, reverse=True)[:limit]


@tool("kill_process", "Force-terminate a process by pid or exact name (unsaved work is lost).",
      {"pid": {"type": "integer"}, "name": {"type": "string"}}, category="destructive", group="desktop")
def kill_process(ctx: ToolContext, pid: int = 0, name: str = "") -> str:
    targets = []
    if pid:
        targets = [psutil.Process(pid)]
    elif name:
        targets = [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == name.lower()]
    if not targets:
        raise ToolError("no matching process")
    protected = {"explorer.exe", "csrss.exe", "winlogon.exe", "lsass.exe", "services.exe", "svchost.exe",
                 "system", "smss.exe", "wininit.exe", "dwm.exe"}
    killed = []
    for p in targets:
        if p.name().lower() in protected or p.pid == os.getpid():
            raise ToolError(f"refusing to kill protected process {p.name()}")
        p.kill()
        killed.append(f"{p.name()}({p.pid})")
    return "killed " + ", ".join(killed)


# ----------------------------------------------------------------- input
def _clipboard_get() -> str:
    import pyperclip

    try:
        return pyperclip.paste()
    except Exception:  # noqa: BLE001
        return ""


def _clipboard_set(text: str) -> None:
    import pyperclip

    pyperclip.copy(text)


@tool("type_text", "Type text into the focused window (or focus a window first via 'window'). "
      "Uses clipboard paste for reliability with any characters; restores the clipboard after.",
      {"text": {"type": "string"}, "window": {"type": "string"}, "press_enter": {"type": "boolean"}},
      required=["text"], category="execute", group="desktop")
def type_text(ctx: ToolContext, text: str, window: str = "", press_enter: bool = False) -> ToolResult:
    import pyautogui

    if window:
        w = find_window(window)
        if not w:
            raise ToolError(f"no window matches '{window}'")
        focus_hwnd(w["hwnd"])
    fg = active_window()
    if fg and "jarvis" in fg["title"].lower() and fg["pid"] == os.getpid():
        raise ToolError("JARVIS itself is focused; specify the target window")
    saved = _clipboard_get()
    _clipboard_set(text)
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    if press_enter:
        pyautogui.press("enter")
    time.sleep(0.1)
    _clipboard_set(saved)
    return ToolResult(True, output=f"typed {len(text)} chars into '{fg['title'] if fg else 'focused window'}'")


@tool("press_keys", "Press a key or shortcut, e.g. 'enter', 'ctrl+s', 'alt+tab', 'win+d'.",
      {"keys": {"type": "string"}, "repeat": {"type": "integer"}}, required=["keys"], category="execute",
      group="desktop")
def press_keys(ctx: ToolContext, keys: str, repeat: int = 1) -> str:
    import pyautogui

    parts = [k.strip().lower() for k in re.split(r"\+", keys) if k.strip()]
    mapping = {"win": "winleft", "windows": "winleft", "control": "ctrl", "escape": "esc", "return": "enter"}
    parts = [mapping.get(p, p) for p in parts]
    for _ in range(max(1, min(repeat, 50))):
        if len(parts) == 1:
            pyautogui.press(parts[0])
        else:
            pyautogui.hotkey(*parts)
        time.sleep(0.05)
    return f"pressed {keys}" + (f" x{repeat}" if repeat > 1 else "")


@tool("mouse_click", "Click at absolute screen coordinates. Only use coordinates obtained from "
      "find_ui_element or find_text_on_screen in this session.",
      {"x": {"type": "integer"}, "y": {"type": "integer"},
       "button": {"type": "string", "enum": ["left", "right", "middle"]}, "double": {"type": "boolean"}},
      required=["x", "y"], category="execute", group="desktop")
def mouse_click(ctx: ToolContext, x: int, y: int, button: str = "left", double: bool = False) -> str:
    import pyautogui

    pyautogui.click(x=x, y=y, button=button, clicks=2 if double else 1, interval=0.08)
    return f"clicked {button} at ({x},{y})"


@tool("scroll", "Scroll the mouse wheel in the focused window (positive = up).",
      {"amount": {"type": "integer"}}, required=["amount"], category="execute", group="desktop")
def scroll(ctx: ToolContext, amount: int) -> str:
    import pyautogui

    pyautogui.scroll(amount * 120)
    return f"scrolled {amount}"


# ------------------------------------------------------------- UI Automation
def _uia_root(window: str) -> Any:
    import uiautomation as auto

    if window:
        w = find_window(window)
        if not w:
            raise ToolError(f"no window matches '{window}'")
        return auto.ControlFromHandle(w["hwnd"])
    return auto.GetForegroundControl().GetTopLevelControl()


def _walk(ctrl: Any, depth: int, max_depth: int, out: list[dict[str, Any]], limit: int) -> None:
    if depth > max_depth or len(out) >= limit:
        return
    try:
        children = ctrl.GetChildren()
    except Exception:  # noqa: BLE001
        return
    for c in children:
        if len(out) >= limit:
            return
        try:
            name = c.Name or ""
            ctype = c.ControlTypeName
            r = c.BoundingRectangle
            if (name or ctype in ("EditControl", "ButtonControl")) and r.width() > 0:
                out.append({"name": name[:80], "type": ctype.replace("Control", ""),
                            "automation_id": c.AutomationId, "enabled": bool(c.IsEnabled),
                            "center": [(r.left + r.right) // 2, (r.top + r.bottom) // 2]})
        except Exception:  # noqa: BLE001
            continue
        _walk(c, depth + 1, max_depth, out, limit)


@tool("list_ui_elements", "List accessible UI controls (buttons, fields, menus, tabs...) of a window "
      "using Windows UI Automation.",
      {"window": {"type": "string", "description": "Window title/process; default foreground"},
       "filter": {"type": "string", "description": "Only controls whose name contains this"},
       "limit": {"type": "integer"}}, group="desktop", timeout=40)
def list_ui_elements(ctx: ToolContext, window: str = "", filter: str = "", limit: int = 120) -> dict[str, Any]:  # noqa: A002
    import uiautomation as auto

    with auto.UIAutomationInitializerInThread():
        root = _uia_root(window)
        out: list[dict[str, Any]] = []
        _walk(root, 0, 12, out, 600)
    if filter:
        out = [e for e in out if filter.lower() in e["name"].lower()]
    return {"window": root.Name, "count": len(out), "elements": out[:limit]}


@tool("click_ui_element", "Find a control by its visible name (and optional type) in a window via UI "
      "Automation and activate it. Falls back to OCR text search on screen.",
      {"name": {"type": "string"}, "window": {"type": "string"},
       "control_type": {"type": "string", "description": "e.g. Button, MenuItem, TabItem, Hyperlink, Edit"},
       "double": {"type": "boolean"}},
      required=["name"], category="execute", group="desktop", timeout=40)
def click_ui_element(ctx: ToolContext, name: str, window: str = "", control_type: str = "",
                     double: bool = False) -> ToolResult:
    import pyautogui
    import uiautomation as auto

    target = None
    with auto.UIAutomationInitializerInThread():
        root = _uia_root(window)
        if window:
            w = find_window(window)
            if w:
                focus_hwnd(w["hwnd"])
        elems: list[dict[str, Any]] = []
        _walk(root, 0, 14, elems, 1500)
        q = name.lower().strip()
        cands = [e for e in elems if e["enabled"] and (not control_type or e["type"].lower() == control_type.lower())]
        exact = [e for e in cands if e["name"].lower() == q]
        partial = [e for e in cands if q in e["name"].lower()]
        target = (exact or partial or [None])[0]
        if target is not None:
            ctrl = auto.ControlFromPoint(*target["center"])
            invoked = False
            try:
                pattern = ctrl.GetInvokePattern() if ctrl else None
                if pattern and not double:
                    pattern.Invoke()
                    invoked = True
            except Exception:  # noqa: BLE001
                invoked = False
            if not invoked:
                pyautogui.click(*target["center"], clicks=2 if double else 1, interval=0.08)
            return ToolResult(True, output=f"activated {target['type']} '{target['name']}' "
                              f"via {'UIA invoke' if invoked else 'click'} at {target['center']}", data=target)
    # OCR fallback
    from jarvis.services.screen import ScreenService

    match = ScreenService.find_text(name)
    if match:
        pyautogui.click(*match["center"], clicks=2 if double else 1, interval=0.08)
        return ToolResult(True, output=f"clicked text '{match['text']}' found by OCR at {match['center']}",
                          data=match)
    raise ToolError(f"could not find a control or on-screen text matching '{name}'")


# ----------------------------------------------------------- media & volume
_VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2, "mute": 0xAD,
       "volume_down": 0xAE, "volume_up": 0xAF}


def _vk(code: int) -> None:
    user32 = ctypes.windll.user32
    user32.keybd_event(code, 0, 0, 0)
    user32.keybd_event(code, 0, 2, 0)


@tool("media_control", "Control media playback in whatever app is playing (Spotify, YouTube, etc).",
      {"action": {"type": "string", "enum": list(_VK)}}, required=["action"], category="execute", group="desktop")
def media_control(ctx: ToolContext, action: str) -> str:
    _vk(_VK[action])
    return f"sent media key: {action}"


def _endpoint_volume() -> Any:
    from pycaw.pycaw import AudioUtilities

    dev = AudioUtilities.GetSpeakers()
    return dev.EndpointVolume


@tool("set_volume", "Set the system master volume (0-100) or mute/unmute.",
      {"level": {"type": "integer"}, "mute": {"type": "boolean"}}, category="execute", group="desktop")
def set_volume(ctx: ToolContext, level: int = -1, mute: bool | None = None) -> str:
    import comtypes

    comtypes.CoInitialize()
    try:
        vol = _endpoint_volume()
        if mute is not None:
            vol.SetMute(1 if mute else 0, None)
        if 0 <= level <= 100:
            vol.SetMasterVolumeLevelScalar(level / 100.0, None)
        now = round(vol.GetMasterVolumeLevelScalar() * 100)
        muted = bool(vol.GetMute())
    finally:
        comtypes.CoUninitialize()
    return f"volume is {now}%{' (muted)' if muted else ''}"


@tool("get_volume", "Get the current system volume.", group="desktop")
def get_volume(ctx: ToolContext) -> str:
    return set_volume(ctx)


# ----------------------------------------------------------------- web & misc
@tool("open_url", "Open a URL (or a search for text) in the default browser.",
      {"url": {"type": "string"}}, required=["url"], category="execute", group="desktop")
def open_url(ctx: ToolContext, url: str) -> str:
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        if "." in url and " " not in url:
            url = "https://" + url
        else:
            from urllib.parse import quote_plus

            url = "https://www.google.com/search?q=" + quote_plus(url)
    if not url.lower().startswith(("http://", "https://")):
        raise ToolError("only http(s) URLs can be opened")
    webbrowser.open(url)
    return f"opened {url}"


_SETTINGS = {
    "display": "ms-settings:display", "sound": "ms-settings:sound", "bluetooth": "ms-settings:bluetooth",
    "wifi": "ms-settings:network-wifi", "network": "ms-settings:network", "notifications": "ms-settings:notifications",
    "power": "ms-settings:powersleep", "battery": "ms-settings:batterysaver", "storage": "ms-settings:storagesense",
    "apps": "ms-settings:appsfeatures", "default apps": "ms-settings:defaultapps", "update": "ms-settings:windowsupdate",
    "privacy": "ms-settings:privacy", "microphone": "ms-settings:privacy-microphone", "camera": "ms-settings:privacy-webcam",
    "personalization": "ms-settings:personalization", "background": "ms-settings:personalization-background",
    "colors": "ms-settings:colors", "mouse": "ms-settings:mousetouchpad", "keyboard": "ms-settings:typing",
    "time": "ms-settings:dateandtime", "language": "ms-settings:regionlanguage", "about": "ms-settings:about",
    "focus": "ms-settings:quiethours", "night light": "ms-settings:nightlight", "vpn": "ms-settings:network-vpn",
}


@tool("open_settings", "Open a Windows Settings page: " + ", ".join(_SETTINGS),
      {"page": {"type": "string"}}, required=["page"], category="execute", group="desktop")
def open_settings(ctx: ToolContext, page: str) -> str:
    uri = _SETTINGS.get(page.lower().strip())
    if not uri:
        close = [k for k in _SETTINGS if page.lower() in k or k in page.lower()]
        uri = _SETTINGS[close[0]] if close else "ms-settings:"
    os.startfile(uri)  # type: ignore[attr-defined]
    return f"opened {uri}"


@tool("clipboard", "Read the clipboard, or write text to it.",
      {"action": {"type": "string", "enum": ["read", "write"]}, "text": {"type": "string"}},
      required=["action"], category="read", group="desktop")
def clipboard(ctx: ToolContext, action: str, text: str = "") -> str:
    if action == "write":
        _clipboard_set(text)
        return f"copied {len(text)} chars to clipboard"
    value = _clipboard_get()
    return value[:8000] if value else "(clipboard is empty or not text)"


@tool("lock_workstation", "Lock the Windows session.", category="system", group="desktop")
def lock_workstation(ctx: ToolContext) -> str:
    ctypes.windll.user32.LockWorkStation()
    return "workstation locked"


@tool("recent_downloads", "List the most recent files in the Downloads folder.", {"limit": {"type": "integer"}},
      group="desktop")
def recent_downloads(ctx: ToolContext, limit: int = 10) -> list[dict[str, Any]]:
    d = Path.home() / "Downloads"
    files = sorted((p for p in d.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "path": str(p), "size": p.stat().st_size,
             "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))} for p in files[:limit]]
