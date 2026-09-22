"""Application and system control for JARVIS — open, close, list applications."""

from __future__ import annotations

import subprocess
import os
from typing import Any

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


# Allowlisted applications that can be opened
APPLICATIONS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "paint": "mspaint.exe",
    "browser": None,  # Special handling
    "chrome": "chrome.exe",
    "firefox": "firefox.exe",
    "edge": "msedge.exe",
    "spotify": "spotify.exe",
    "vscode": "code.exe",
    "code": "code.exe",
    "terminal": "cmd.exe",
    "command prompt": "cmd.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "settings": "ms-settings:",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "discord": "discord.exe",
    "slack": "slack.exe",
    "zoom": "zoom.exe",
    "steam": "steam.exe",
    "antigravity": "Antigravity IDE.exe",
    "antigravity ide": "Antigravity IDE.exe",
    "antigravity id": "Antigravity IDE.exe",
    "chatgpt": "ChatGPT.exe",
}


KNOWN_APP_PATHS: dict[str, list[str]] = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "firefox": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    ],
    "brave": [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
    ],
    "vscode": [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
        r"C:\Program Files\Microsoft VS Code\Code.exe",
    ],
    "code": [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
        r"C:\Program Files\Microsoft VS Code\Code.exe",
    ],
    "spotify": [
        os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe"),
    ],
    "antigravity": [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Antigravity IDE\Antigravity IDE.exe"),
    ],
    "antigravity ide": [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Antigravity IDE\Antigravity IDE.exe"),
    ],
    "antigravity id": [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Antigravity IDE\Antigravity IDE.exe"),
    ],
    "chatgpt": [
        r"C:\Program Files\WindowsApps\OpenAI.Codex_26.917.9434.0_x64__2p2nqsd0c76g0\app\ChatGPT.exe",
    ],
}


def attach_desktop() -> None:
    """Ensure the calling thread is attached to the active interactive desktop."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hinput = user32.OpenInputDesktop(0, False, 0x01FF)
        if hinput:
            user32.SetThreadDesktop(hinput)
    except Exception:
        pass


def bring_window_to_front(hwnd: int) -> None:
    """Restore and force a window to the foreground on the user's screen.

    Windows prevents background processes from calling SetForegroundWindow.
    The workaround: simulate an Alt keypress (releases the foreground lock),
    then attach our thread input to the foreground thread, then set foreground.
    """
    attach_desktop()
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # 1. If minimized, restore first
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE

        # 2. Simulate Alt keypress to release the foreground lock
        user32.keybd_event(0x12, 0, 0x0001, 0)  # Alt down (EXTENDEDKEY)
        user32.keybd_event(0x12, 0, 0x0003, 0)  # Alt up   (EXTENDEDKEY | KEYUP)

        # 3. Attach our thread to the foreground window's thread
        cur_thread = kernel32.GetCurrentThreadId()
        fore_hwnd = user32.GetForegroundWindow()
        fore_thread = user32.GetWindowThreadProcessId(fore_hwnd, None)
        attached = False
        if cur_thread != fore_thread:
            attached = bool(user32.AttachThreadInput(cur_thread, fore_thread, True))

        # 4. Now SetForegroundWindow should succeed
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.ShowWindow(hwnd, 5)  # SW_SHOW

        # 5. Detach
        if attached:
            user32.AttachThreadInput(cur_thread, fore_thread, False)
    except Exception:
        pass


def focus_window_by_keyword(*keywords: str) -> tuple[bool, str]:
    """Find an existing window matching any of the given keywords and bring it to front.

    Uses raw ctypes EnumWindows to find ALL windows including UWP apps
    (modern Notepad, Calculator, etc.) which pygetwindow cannot see.
    """
    attach_desktop()
    import ctypes
    user32 = ctypes.windll.user32
    lowers = [k.lower() for k in keywords if k]

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    found_hwnd = None
    found_title = ""

    def enum_cb(hwnd, lparam):
        nonlocal found_hwnd, found_title
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        title = buff.value
        if title and any(k in title.lower() for k in lowers):
            found_hwnd = hwnd
            found_title = title
            return False  # Stop enumeration
        return True

    try:
        user32.EnumWindows(EnumWindowsProc(enum_cb), 0)
    except Exception:
        pass

    if found_hwnd:
        bring_window_to_front(found_hwnd)
        return True, found_title
    return False, ""


def launch(application: str) -> str:
    """Launch an application or switch to it if already running, bringing it to foreground."""
    import time
    name = application.strip().lower()

    # 1. Map target name to search keywords and launch command
    #    UWP apps (notepad, calculator) MUST be launched via explorer.exe shell:AppsFolder
    #    because subprocess/os.startfile creates ghost processes with no visible window.
    app_meta = {
        "chrome": {
            "keywords": ["chrome", "google chrome"],
            "launch": lambda: subprocess.Popen([r"C:\Program Files\Google\Chrome\Application\chrome.exe"]),
            "fallback": lambda: os.startfile("chrome"),
        },
        "notepad": {
            "keywords": ["notepad", "untitled"],
            "launch": lambda: subprocess.Popen([
                "explorer.exe",
                r"shell:AppsFolder\Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"
            ]),
        },
        "calculator": {
            "keywords": ["calculator"],
            "launch": lambda: subprocess.Popen([
                "explorer.exe",
                r"shell:AppsFolder\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"
            ]),
        },
        "spotify": {
            "keywords": ["spotify"],
            "launch": lambda: os.startfile("spotify:"),
        },
        "vscode": {
            "keywords": ["visual studio code", "code"],
            "launch": lambda: subprocess.Popen(["cmd.exe", "/c", "start", "code"], shell=False),
        },
        "settings": {
            "keywords": ["settings"],
            "launch": lambda: os.startfile("ms-settings:"),
        },
        "task manager": {
            "keywords": ["task manager"],
            "launch": lambda: subprocess.Popen(["cmd.exe", "/c", "start", "taskmgr"], shell=False),
        },
        "explorer": {
            "keywords": ["file explorer", "explorer"],
            "launch": lambda: subprocess.Popen(["cmd.exe", "/c", "start", "explorer"], shell=False),
        },
        "antigravity": {
            "keywords": ["antigravity", "antigravity ide"],
            "launch": lambda: subprocess.Popen([os.path.expandvars(r"%LOCALAPPDATA%\Programs\Antigravity IDE\Antigravity IDE.exe")]),
            "fallback": lambda: os.startfile(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Antigravity IDE\Antigravity IDE.exe")),
        },
        "antigravity ide": {
            "keywords": ["antigravity", "antigravity ide"],
            "launch": lambda: subprocess.Popen([os.path.expandvars(r"%LOCALAPPDATA%\Programs\Antigravity IDE\Antigravity IDE.exe")]),
            "fallback": lambda: os.startfile(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Antigravity IDE\Antigravity IDE.exe")),
        },
        "antigravity id": {
            "keywords": ["antigravity", "antigravity ide"],
            "launch": lambda: subprocess.Popen([os.path.expandvars(r"%LOCALAPPDATA%\Programs\Antigravity IDE\Antigravity IDE.exe")]),
            "fallback": lambda: os.startfile(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Antigravity IDE\Antigravity IDE.exe")),
        },
        "chatgpt": {
            "keywords": ["chatgpt"],
            "launch": lambda: os.startfile(r"C:\Program Files\WindowsApps\OpenAI.Codex_26.917.9434.0_x64__2p2nqsd0c76g0\app\ChatGPT.exe"),
            "fallback": lambda: os.startfile("https://chatgpt.com"),
        },
    }

    meta = None
    for k, v in app_meta.items():
        if name == k or k in name or name in k:
            meta = v
            break

    search_keywords = meta["keywords"] if meta else [name]

    # 2. Attach to the interactive desktop first
    attach_desktop()

    # 3. If already open, bring it to the foreground immediately!
    found, title = focus_window_by_keyword(*search_keywords)
    if found:
        return f"Switched to {title}."

    # 4. Launch the application
    launched = False

    # Try the meta launch command first (most reliable)
    if meta and "launch" in meta:
        try:
            meta["launch"]()
            launched = True
        except Exception:
            if meta.get("fallback"):
                try:
                    meta["fallback"]()
                    launched = True
                except Exception:
                    pass

    # Fall back to known paths
    if not launched:
        for key, paths in KNOWN_APP_PATHS.items():
            if name == key or key in name:
                for p in paths:
                    if os.path.exists(p):
                        try:
                            subprocess.Popen([p])
                            launched = True
                            break
                        except Exception:
                            pass
                if launched:
                    break

    # Fall back to APPLICATIONS dict or bare name
    if not launched:
        command = APPLICATIONS.get(name, name)
        try:
            os.startfile(command or name)
            launched = True
        except Exception:
            pass

    if not launched:
        try:
            subprocess.Popen(["cmd.exe", "/c", "start", name], shell=False)
            launched = True
        except Exception:
            pass

    if not launched:
        raise RuntimeError(f"'{name}' could not be launched. Check if it is installed.")

    # 5. Wait for window to appear, then bring it to the front
    for attempt in range(25):  # 5 seconds total
        time.sleep(0.2)
        attach_desktop()  # re-attach each poll in case desktop changed
        found, title = focus_window_by_keyword(*search_keywords)
        if found:
            return f"Opened {title}."

    return f"Opened {name}."


def close(application: str) -> str:
    """Close an application by name."""
    if not HAS_PSUTIL:
        raise RuntimeError("Process management requires 'psutil'. Install it with: pip install psutil")

    name = application.strip().lower()
    target = APPLICATIONS.get(name, name)
    if target.endswith(".exe"):
        target_name = target[:-4]
    else:
        target_name = name

    closed = []
    for proc in psutil.process_iter(['name', 'pid']):
        try:
            proc_name = proc.info['name']
            if proc_name and target_name in proc_name.lower():
                proc.terminate()
                closed.append(proc_name)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not closed:
        raise RuntimeError(f"No running process matching '{name}' was found.")
    unique = list(dict.fromkeys(closed))
    return f"Closed {', '.join(unique)}."


def list_running() -> str:
    """List currently running applications."""
    if not HAS_PSUTIL:
        raise RuntimeError("Process management requires 'psutil'. Install it with: pip install psutil")

    apps: dict[str, int] = {}
    for proc in psutil.process_iter(['name']):
        try:
            name = proc.info['name']
            if name and name.endswith('.exe'):
                short = name[:-4].capitalize()
                apps[short] = apps.get(short, 0) + 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not apps:
        return "No applications detected."

    sorted_apps = sorted(apps.items(), key=lambda x: x[1], reverse=True)
    lines = ["Running applications:"]
    for name, count in sorted_apps[:20]:
        lines.append(f"  {name}" + (f" (x{count})" if count > 1 else ""))
    return "\n".join(lines)


def get_system_info() -> str:
    """Get basic system information."""
    if not HAS_PSUTIL:
        raise RuntimeError("System info requires 'psutil'. Install it with: pip install psutil")

    import platform
    cpu_percent = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(os.path.expanduser("~"))[:3])

    lines = [
        f"OS: {platform.system()} {platform.release()}",
        f"CPU usage: {cpu_percent}%",
        f"Memory: {memory.percent}% used ({memory.used // (1024**3)}GB / {memory.total // (1024**3)}GB)",
        f"Disk: {disk.percent}% used ({disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB)",
    ]
    return "\n".join(lines)
