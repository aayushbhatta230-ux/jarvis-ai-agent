"""
Remote PC Controller for JARVIS
================================
Provides full remote mouse, keyboard, display, hardware, application,
screen recording, and device location capabilities.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from typing import Any

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None


def attach_desktop() -> None:
    """Ensure the calling thread is attached to the active interactive desktop."""
    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            hinput = user32.OpenInputDesktop(0, False, 0x01FF)
            if hinput:
                user32.SetThreadDesktop(hinput)
        except Exception:
            pass


def get_screen_size() -> tuple[int, int]:
    """Get primary monitor resolution in pixels."""
    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        except Exception:
            pass
    if pyautogui:
        return pyautogui.size()
    return (1920, 1080)


def execute_remote_action(action: str, **kwargs: Any) -> dict[str, Any]:
    """Execute a deterministic remote PC control command."""
    attach_desktop()
    act = (action or "").strip().lower()

    # ---------------------------------------------------------
    # 1. Mouse & Screen Cursor Control
    # ---------------------------------------------------------
    if act in ("click", "mouse_click", "left_click", "right_click", "double_click"):
        if not pyautogui:
            return {"ok": False, "error": "pyautogui unavailable"}
        
        button = kwargs.get("button", "left")
        if act == "right_click":
            button = "right"
        clicks = kwargs.get("clicks", 2 if act == "double_click" else 1)

        x = kwargs.get("x")
        y = kwargs.get("y")

        # Support normalized coordinates (0.0 to 1.0) from mobile touchscreen
        if kwargs.get("normalized", False) or (isinstance(x, float) and 0.0 <= x <= 1.0 and isinstance(y, float) and 0.0 <= y <= 1.0):
            sw, sh = get_screen_size()
            x = int(x * sw)
            y = int(y * sh)

        target_label = kwargs.get("label") or kwargs.get("target")
        if target_label and x is None and y is None:
            try:
                from perception.tabs import find_and_click_target
                res = find_and_click_target(str(target_label))
                if res.get("ok"):
                    return {
                        "ok": True,
                        "action": act,
                        "spoken": res.get("message", f"Clicked {target_label}, sir."),
                        "message": res.get("message", f"Clicked {target_label}"),
                    }
                return {"ok": False, "action": act, "error": res.get("error", f"Could not find {target_label}")}
            except Exception as exc:
                return {"ok": False, "action": act, "error": str(exc)}

        if x is not None and y is not None:
            pyautogui.click(x=int(x), y=int(y), button=button, clicks=clicks)
            coord_str = f" at ({int(x)}, {int(y)})"
        else:
            pyautogui.click(button=button, clicks=clicks)
            coord_str = ""

        spoken = f"Clicked{coord_str}, sir."
        return {"ok": True, "action": act, "spoken": spoken, "message": f"Mouse {button}-click{coord_str}"}

    if act in ("tab_click", "click_tab", "click_menu", "click_element", "switch", "switch_app", "focus"):
        target_label = kwargs.get("target") or kwargs.get("label") or kwargs.get("text") or ""
        if not target_label:
            return {"ok": False, "error": "Missing target text for action"}
        try:
            from perception.tabs import find_and_click_target
            res = find_and_click_target(str(target_label))
            if res.get("ok"):
                return {
                    "ok": True,
                    "action": act,
                    "spoken": res.get("message", f"Done, sir."),
                    "message": res.get("message", f"Done."),
                }
            return {"ok": False, "action": act, "error": res.get("error", f"Could not find {target_label}")}
        except Exception as exc:
            return {"ok": False, "action": act, "error": str(exc)}

    if act in ("move", "mouse_move"):
        if not pyautogui:
            return {"ok": False, "error": "pyautogui unavailable"}
        x = kwargs.get("x")
        y = kwargs.get("y")
        if kwargs.get("normalized", False) or (isinstance(x, float) and 0.0 <= x <= 1.0 and isinstance(y, float) and 0.0 <= y <= 1.0):
            sw, sh = get_screen_size()
            x = int(x * sw)
            y = int(y * sh)
        if x is not None and y is not None:
            pyautogui.moveTo(int(x), int(y))
            return {"ok": True, "action": act, "spoken": f"Moved cursor to {int(x)}, {int(y)}, sir.", "message": f"Cursor moved to ({int(x)}, {int(y)})"}
        return {"ok": False, "error": "Missing x, y coordinates"}

    if act in ("mouse_down", "mousedown", "touch_down"):
        if not pyautogui:
            return {"ok": False, "error": "pyautogui unavailable"}
        button = kwargs.get("button", "left")
        x = kwargs.get("x")
        y = kwargs.get("y")
        if kwargs.get("normalized", False) or (isinstance(x, float) and 0.0 <= x <= 1.0 and isinstance(y, float) and 0.0 <= y <= 1.0):
            sw, sh = get_screen_size()
            x = int(x * sw)
            y = int(y * sh)
        if x is not None and y is not None:
            pyautogui.mouseDown(x=int(x), y=int(y), button=button)
        else:
            pyautogui.mouseDown(button=button)
        return {"ok": True, "action": act}

    if act in ("mouse_up", "mouseup", "touch_up"):
        if not pyautogui:
            return {"ok": False, "error": "pyautogui unavailable"}
        button = kwargs.get("button", "left")
        x = kwargs.get("x")
        y = kwargs.get("y")
        if kwargs.get("normalized", False) or (isinstance(x, float) and 0.0 <= x <= 1.0 and isinstance(y, float) and 0.0 <= y <= 1.0):
            sw, sh = get_screen_size()
            x = int(x * sw)
            y = int(y * sh)
        if x is not None and y is not None:
            pyautogui.mouseUp(x=int(x), y=int(y), button=button)
        else:
            pyautogui.mouseUp(button=button)
        return {"ok": True, "action": act}

    if act in ("drag", "drag_to", "select_drag"):
        if not pyautogui:
            return {"ok": False, "error": "pyautogui unavailable"}
        x = kwargs.get("x")
        y = kwargs.get("y")
        if kwargs.get("normalized", False) or (isinstance(x, float) and 0.0 <= x <= 1.0 and isinstance(y, float) and 0.0 <= y <= 1.0):
            sw, sh = get_screen_size()
            x = int(x * sw)
            y = int(y * sh)
        if x is not None and y is not None:
            pyautogui.dragTo(int(x), int(y), duration=kwargs.get("duration", 0.08), button=kwargs.get("button", "left"))
            return {"ok": True, "action": act}
        return {"ok": False, "error": "Missing coordinates for drag"}

    if act in ("scroll", "mouse_scroll", "scroll_down", "scroll_up"):
        if not pyautogui:
            return {"ok": False, "error": "pyautogui unavailable"}
        direction = kwargs.get("direction", "down" if "down" in act else "up")
        raw_amount = kwargs.get("amount", kwargs.get("clicks", 3))
        try:
            raw_val = int(raw_amount)
        except Exception:
            raw_val = 3

        # Position mouse over target content so Windows routes MOUSEEVENTF_WHEEL to the active window!
        sw, sh = get_screen_size()
        target_x = kwargs.get("x")
        target_y = kwargs.get("y")
        try:
            if target_x is not None and target_y is not None:
                if kwargs.get("normalized", False) or (isinstance(target_x, float) and 0.0 <= target_x <= 1.0):
                    target_x = int(target_x * sw)
                    target_y = int(target_y * sh)
                else:
                    target_x = int(target_x)
                    target_y = int(target_y)
            else:
                cur_x, cur_y = pyautogui.position()
                if cur_x <= 15 or cur_y <= 15:
                    target_x = sw // 2
                    target_y = sh // 2
                else:
                    target_x = cur_x
                    target_y = cur_y
        except Exception as pos_err:
            target_x = sw // 2
            target_y = sh // 2

        # On Windows, 1 mouse wheel notch = 120 delta units.
        wheel_delta = raw_val if abs(raw_val) >= 60 else (raw_val * 120)
        clicks = -abs(wheel_delta) if direction == "down" else abs(wheel_delta)
        try:
            pyautogui.scroll(clicks, x=target_x, y=target_y)
        except Exception:
            pyautogui.scroll(clicks)
        return {"ok": True, "action": act, "spoken": f"Scrolled {direction}, sir.", "message": f"Scrolled {direction} ({abs(raw_val)} lines)"}

    # ---------------------------------------------------------
    # 2. Keyboard & Typing
    # ---------------------------------------------------------
    if act in ("type", "type_text", "write"):
        if not pyautogui:
            return {"ok": False, "error": "pyautogui unavailable"}
        text = kwargs.get("text", "")
        press_enter = kwargs.get("enter", False)
        if text:
            # For complex unicode or fast input, copy to clipboard and paste
            try:
                import pyperclip
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
            except Exception:
                pyautogui.write(text, interval=0.01)
            if press_enter:
                time.sleep(0.05)
                pyautogui.press("enter")
            return {"ok": True, "action": act, "spoken": f"Typed '{text[:20]}', sir.", "message": f"Typed: {text}"}
        return {"ok": False, "error": "No text provided to type"}

    if act in ("press_key", "key", "press"):
        if not pyautogui:
            return {"ok": False, "error": "pyautogui unavailable"}
        key = (kwargs.get("key") or "").strip().lower()
        if key:
            pyautogui.press(key)
            return {"ok": True, "action": act, "spoken": f"Pressed {key}, sir.", "message": f"Pressed key: {key}"}
        return {"ok": False, "error": "No key specified"}

    if act in ("hotkey", "shortcut"):
        if not pyautogui:
            return {"ok": False, "error": "pyautogui unavailable"}
        keys = kwargs.get("keys", [])
        if isinstance(keys, str):
            keys = [k.strip().lower() for k in keys.replace("+", " ").split()]
        if keys:
            pyautogui.hotkey(*keys)
            combo = "+".join(keys)
            return {"ok": True, "action": act, "spoken": f"Triggered {combo}, sir.", "message": f"Hotkey: {combo}"}
        return {"ok": False, "error": "No hotkey combinations specified"}

    # ---------------------------------------------------------
    # 3. Audio & Volume Control
    # ---------------------------------------------------------
    if act in ("volume_up", "volup", "louder"):
        step = kwargs.get("step", 5)
        if pyautogui:
            for _ in range(step):
                pyautogui.press("volumeup")
            return {"ok": True, "action": act, "spoken": "Volume increased, sir.", "message": "Volume increased (+)"}
        return {"ok": False, "error": "pyautogui unavailable"}

    if act in ("volume_down", "voldown", "quieter"):
        step = kwargs.get("step", 5)
        if pyautogui:
            for _ in range(step):
                pyautogui.press("volumedown")
            return {"ok": True, "action": act, "spoken": "Volume decreased, sir.", "message": "Volume decreased (-)"}
        return {"ok": False, "error": "pyautogui unavailable"}

    if act in ("mute", "unmute", "toggle_mute", "volume_mute"):
        if pyautogui:
            pyautogui.press("volumemute")
            return {"ok": True, "action": act, "spoken": "Audio muted, sir.", "message": "Volume muted/unmuted"}
        return {"ok": False, "error": "pyautogui unavailable"}

    # ---------------------------------------------------------
    # 4. Media Playback
    # ---------------------------------------------------------
    if act in ("play_pause", "play", "pause", "resume", "media_play_pause"):
        if pyautogui:
            pyautogui.press("playpause")
            return {"ok": True, "action": act, "spoken": "Media toggled, sir.", "message": "Media Play/Pause"}
        return {"ok": False, "error": "pyautogui unavailable"}

    if act in ("next_track", "skip", "next_song", "media_next"):
        if pyautogui:
            pyautogui.press("nexttrack")
            return {"ok": True, "action": act, "spoken": "Playing next track, sir.", "message": "Next Track"}
        return {"ok": False, "error": "pyautogui unavailable"}

    if act in ("prev_track", "previous", "previous_song", "media_prev"):
        if pyautogui:
            pyautogui.press("prevtrack")
            return {"ok": True, "action": act, "spoken": "Playing previous track, sir.", "message": "Previous Track"}
        return {"ok": False, "error": "pyautogui unavailable"}

    if act in ("stop_media", "stop"):
        if pyautogui:
            pyautogui.press("stop")
            return {"ok": True, "action": act, "spoken": "Media stopped, sir.", "message": "Media Stopped"}
        return {"ok": False, "error": "pyautogui unavailable"}

    # ---------------------------------------------------------
    # 5. Windows & Desktop Management
    # ---------------------------------------------------------
    if act in ("show_desktop", "minimize_all", "desktop"):
        if pyautogui:
            pyautogui.hotkey("win", "d")
            return {"ok": True, "action": act, "spoken": "Showing desktop, sir.", "message": "Desktop toggled (Win+D)"}
        return {"ok": False, "error": "pyautogui unavailable"}

    if act in ("switch_window", "alt_tab", "next_window"):
        if pyautogui:
            pyautogui.hotkey("alt", "tab")
            return {"ok": True, "action": act, "spoken": "Switched window, sir.", "message": "Switched window (Alt+Tab)"}
        return {"ok": False, "error": "pyautogui unavailable"}

    if act in ("close_window", "close_app"):
        app_name = kwargs.get("app")
        if app_name:
            from tools.computer import close
            try:
                res = close(app_name)
                return {"ok": True, "action": act, "spoken": f"Closed {app_name}, sir.", "message": res}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        if pyautogui:
            pyautogui.hotkey("alt", "f4")
            return {"ok": True, "action": act, "spoken": "Closed active window, sir.", "message": "Closed window (Alt+F4)"}
        return {"ok": False, "error": "pyautogui unavailable"}

    if act in ("maximize_window", "fullscreen"):
        if pyautogui:
            pyautogui.hotkey("win", "up")
            return {"ok": True, "action": act, "spoken": "Window maximized, sir.", "message": "Window maximized"}
        return {"ok": False, "error": "pyautogui unavailable"}

    if act in ("restore_window", "minimize_window"):
        if pyautogui:
            pyautogui.hotkey("win", "down")
            return {"ok": True, "action": act, "spoken": "Window restored, sir.", "message": "Window minimized/restored"}
        return {"ok": False, "error": "pyautogui unavailable"}

    # ---------------------------------------------------------
    # 6. Applications Control
    # ---------------------------------------------------------
    if act in ("open_app", "launch_app", "start_app"):
        app_name = kwargs.get("app") or kwargs.get("name")
        if not app_name:
            return {"ok": False, "error": "No application name specified"}
        try:
            from tools.computer import launch
            res = launch(app_name)
            return {"ok": True, "action": act, "spoken": f"Opened {app_name}, sir.", "message": res}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if act in ("open_path", "open_file", "launch_file"):
        raw_path = kwargs.get("path") or kwargs.get("target") or kwargs.get("file")
        if not raw_path:
            return {"ok": False, "error": "No file path specified"}
        try:
            import os
            from pathlib import Path
            candidate = Path(str(raw_path)).expanduser()
            if not candidate.is_absolute() or not candidate.exists():
                # Fall back to smart file resolution across workspace/user dirs
                from tools.smart_files import find_file
                resolved = find_file(str(raw_path))
                if resolved is not None:
                    candidate = Path(resolved)
            if not candidate.exists():
                return {"ok": False, "error": f"File not found: {raw_path}"}
            os.startfile(str(candidate))
            return {
                "ok": True,
                "action": act,
                "spoken": f"Opening {candidate.name} on your PC, sir.",
                "message": f"Opened {candidate.name} on PC",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---------------------------------------------------------
    # 7. Screen Recording
    # ---------------------------------------------------------
    if act in ("start_recording", "record_screen", "screen_recording"):
        from tools.screen_recorder import start_recording
        duration = kwargs.get("duration")
        res = start_recording(duration=duration)
        if res.get("ok"):
            dur_msg = f" for {duration} seconds" if duration else ""
            return {"ok": True, "action": act, "spoken": f"Screen recording started{dur_msg}, sir.", "message": res["message"]}
        return res

    if act in ("stop_recording", "stop_screen_recording", "save_recording"):
        from tools.screen_recorder import stop_recording
        res = stop_recording()
        if res.get("ok"):
            return {
                "ok": True,
                "action": act,
                "spoken": f"Screen recording completed, sir. Recorded {res.get('elapsed_seconds')} seconds.",
                "message": res["message"],
                "url": res.get("url"),
                "file": res.get("file"),
            }
        return res

    # ---------------------------------------------------------
    # 8. Laptop Locator & Tracking Beacon
    # ---------------------------------------------------------
    if act in ("locate_laptop", "find_laptop", "locate", "laptop_location", "tracking_beacon"):
        from tools.locator import locate_laptop
        beacon = kwargs.get("beacon", True)
        res = locate_laptop(activate_beacon=beacon)
        return {
            "ok": True,
            "action": act,
            "spoken": res["spoken"],
            "message": res["markdown"],
            "data": res,
        }

    # ---------------------------------------------------------
    # 9. System Security & Power
    # ---------------------------------------------------------
    if act in ("lock_pc", "lock", "lock_screen"):
        if sys.platform == "win32":
            ctypes.windll.user32.LockWorkStation()
            return {"ok": True, "action": act, "spoken": "Workstation locked, sir.", "message": "PC Locked"}
        return {"ok": False, "error": "Not on Windows"}

    if act in ("sleep_pc", "sleep"):
        if sys.platform == "win32":
            os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
            return {"ok": True, "action": act, "spoken": "Entering sleep mode, sir.", "message": "PC Sleep"}
        return {"ok": False, "error": "Not on Windows"}

    # ---------------------------------------------------------
    # 10. Screenshot
    # ---------------------------------------------------------
    if act in ("screenshot", "screen"):
        try:
            from tools.screen import capture_screen
            msg = capture_screen()
            return {"ok": True, "action": act, "spoken": "Screenshot captured, sir.", "message": msg}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": f"Unknown remote action: '{action}'"}
