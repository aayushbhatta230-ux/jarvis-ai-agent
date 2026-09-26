"""Autonomous JARVIS Chrome + YouTube & YouTube Music controller."""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.05
except Exception:
    pyautogui = None

try:
    import pygetwindow as gw
except Exception:
    gw = None

YOUTUBE_MUSIC_URL = "https://music.youtube.com"


@dataclass
class MusicState:
    playing: bool = False
    paused: bool = False
    current_track: str | None = None
    source: str | None = None
    tab_ready: bool = False


music_state = MusicState()
_chrome_window = None


def _find_chrome_exe() -> str | None:
    """Find installed Chrome executable."""
    for p in [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
    ]:
        if os.path.exists(p):
            return p
    return None


def _activate_chrome() -> bool:
    """Activate or focus the user's Chrome window."""
    global _chrome_window

    try:
        if _chrome_window and _chrome_window.title:
            if _chrome_window.isMinimized:
                _chrome_window.restore()
            _chrome_window.activate()
            time.sleep(0.2)
            return True
    except Exception:
        _chrome_window = None

    if gw:
        try:
            for window in gw.getAllWindows():
                title = (window.title or "").lower()
                if "chrome" in title or "youtube" in title:
                    if window.isMinimized:
                        window.restore()
                    window.activate()
                    _chrome_window = window
                    time.sleep(0.2)
                    return True
        except Exception:
            pass

    try:
        from tools.computer import focus_window_by_keyword
        if focus_window_by_keyword("youtube", "chrome", "edge", "brave"):
            return True
    except Exception:
        pass

    return False


def _ensure_chrome_open(url: str) -> bool:
    """Ensure Chrome is running and open to the specified URL."""
    from tools.computer import attach_desktop, focus_window_by_keyword
    attach_desktop()

    chrome_running = _activate_chrome()

    if not chrome_running:
        chrome_exe = _find_chrome_exe()
        if chrome_exe:
            try:
                subprocess.Popen([chrome_exe, url])
                time.sleep(2.0)
                focus_window_by_keyword("youtube", "chrome")
                return True
            except Exception as e:
                print(f"[MEDIA] Subprocess Chrome launch error: {e}")

        # Fallback to webbrowser
        import webbrowser
        try:
            webbrowser.open(url)
            time.sleep(1.8)
            focus_window_by_keyword("youtube", "chrome")
            return True
        except Exception as e:
            print(f"[MEDIA] Webbrowser open error: {e}")
            return False

    # Chrome is already running; open URL in current tab or new tab
    if pyautogui:
        try:
            import pyperclip
            pyautogui.hotkey("ctrl", "t")
            time.sleep(0.25)
            pyperclip.copy(url)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.08)
            pyautogui.press("enter")
            time.sleep(0.6)
            return True
        except Exception:
            pass

    import webbrowser
    webbrowser.open(url)
    return True


def _get_ytmusic_track_id(query: str) -> str | None:
    """Use yt_dlp to extract the exact official YouTube Music audio track ID."""
    try:
        import yt_dlp
        ydl_opts = {
            "quiet": True,
            "extract_flat": True,
            "skip_download": True,
            "socket_timeout": 3,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            res = ydl.extract_info(f"ytsearch1:{query} audio", download=False)
            entries = res.get("entries", [])
            if entries:
                return entries[0].get("id")
    except Exception as exc:
        print(f"[MEDIA] yt_dlp lookup note: {exc}")
    return None


def play_music(
    query: str | None = None,
    mood: str = "default",
) -> str:
    """Autonomously search and immediately play music directly on YouTube Music via Chrome."""
    from tools.computer import attach_desktop, focus_window_by_keyword
    attach_desktop()

    if not query:
        mood_lower = (mood or "default").lower()
        mood_queries = {
            "relaxed": "relaxing lofi music",
            "energetic": "energetic workout music",
            "fast": "fast upbeat music",
            "upbeat": "upbeat pop hits",
            "workout": "workout motivation music",
            "hype": "hype bass music",
            "sad": "sad acoustic songs",
            "romantic": "romantic songs",
            "default": "lofi chill beats",
        }
        query = mood_queries.get(mood_lower, "lofi chill beats")

    query_str = str(query).strip()
    # Strip conversational prefixes/suffixes
    query_clean = re.sub(r"^(?:please\s+|can\s+you\s+)?(?:open\s+and\s+play|open\s+|play\s+(?:music\s+|song\s+|a\s+song\s+)?|play\s+|start\s+)", "", query_str, flags=re.I).strip()
    query_clean = re.sub(r"(?:on\s+youtube\s+music|on\s+youtube|in\s+youtube|music|song)$", "", query_clean, flags=re.I).strip()
    if not query_clean or query_clean.lower() in ("music", "youtube music", "yt music", "song", "songs", "some music"):
        query_clean = "lofi chill beats"

    platform_name = "YouTube Music"
    print(f"[MEDIA] Autonomous YouTube Music Request: '{query_clean}'")

    # 1. First attempt: Direct YouTube Music track ID using yt_dlp (plays immediately without search navigation)
    track_id = _get_ytmusic_track_id(query_clean)
    if track_id:
        player_url = f"https://music.youtube.com/watch?v={track_id}"
        print(f"[MEDIA] Direct YouTube Music Track URL: {player_url}")
    else:
        player_url = f"https://music.youtube.com/search?q={urllib.parse.quote_plus(query_clean)}"
        print(f"[MEDIA] YouTube Music Search URL: {player_url}")

    # Launch or navigate Chrome to YouTube Music
    _ensure_chrome_open(player_url)

    # Wait for YouTube Music DOM & audio player to render
    time.sleep(2.2)
    focus_window_by_keyword("youtube", "chrome")
    time.sleep(0.3)

    # Autonomous Mouse Activation & Click:
    if pyautogui:
        try:
            pyautogui.FAILSAFE = False
            w, h = pyautogui.size()
            if track_id:
                # Direct player loaded:
                # YouTube Music player bar Play button is pinned at bottom left:
                # (105, 976) on 1920x1080 -> (w * 0.055, h * 0.904)
                play_x = int(w * 0.055)
                play_y = int(h * 0.904)
                print(f"[MEDIA] Autonomous mouse glide to YouTube Music play button ({play_x}, {play_y})")
                pyautogui.moveTo(play_x, play_y, duration=0.35)
                time.sleep(0.15)
                pyautogui.click()
                print("[MEDIA] Play button clicked successfully!")
            else:
                # Search results page: click Top Result card play button, then first Song row
                print(f"[MEDIA] Autonomous mouse glide to search result card ({int(w * 0.22)}, {int(h * 0.28)})")
                pyautogui.moveTo(int(w * 0.22), int(h * 0.28), duration=0.35)
                pyautogui.click()
                time.sleep(0.3)
                pyautogui.moveTo(int(w * 0.45), int(h * 0.22), duration=0.25)
                pyautogui.click()
                time.sleep(0.2)
                pyautogui.press("enter")
        except Exception as act_err:
            print(f"[MEDIA] Autonomous mouse click error: {act_err}")

    music_state.playing = True
    music_state.paused = False
    music_state.current_track = query_clean
    music_state.source = platform_name
    music_state.tab_ready = True

    return f"Playing '{query_clean}' on YouTube Music, sir."


# ============================================================================
# MEDIA CONTROLS
# ============================================================================

def _media_key(vk: int):
    """Send a Windows hardware media key."""
    try:
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
    except Exception:
        pass


def stop_music() -> str:
    """Stop media playback."""
    _media_key(0xB2)
    music_state.playing = False
    music_state.paused = False
    return "Music stopped."


def pause_music() -> str:
    """Pause media playback."""
    _media_key(0xB3)
    music_state.playing = False
    music_state.paused = True
    return "Music paused."


def resume_music() -> str:
    """Resume media playback."""
    _media_key(0xB3)
    music_state.playing = True
    music_state.paused = False
    return "Music resumed."


def next_music() -> str:
    """Skip to the next track."""
    _media_key(0xB0)
    return "Skipped to the next track."


def get_music_state() -> dict:
    """Return current music state."""
    return {
        "playing": music_state.playing,
        "paused": music_state.paused,
        "current_track": music_state.current_track,
        "source": music_state.source,
        "tab_ready": music_state.tab_ready,
    }