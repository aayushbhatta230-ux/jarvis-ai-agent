"""Real-time control of what's playing / showing on screen.

"Skip the ad", "pause", "next video", "full screen", "click subscribe" ... act on the
video or page the user is looking at right now. Controls are located through Windows
UI Automation (the browser's accessibility tree), with OCR as the fallback, and every
action is checked afterwards.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import webbrowser
from typing import TYPE_CHECKING, Any

from jarvis.tools.registry import ToolContext, ToolError, ToolResult, tool

if TYPE_CHECKING:
    from jarvis.app import Jarvis

log = logging.getLogger(__name__)

BROWSERS = ("chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe", "vivaldi.exe")
PLAYERS = ("vlc.exe", "spotify.exe", "wmplayer.exe", "music.ui.exe", "video.ui.exe", "netflix.exe")
_SKIP = re.compile(r"(?i)^\s*(skip|skip ad|skip ads|skip advertisement|skip ad\(s\))\s*$|\bskip ads?\b")
_AD_HINT = re.compile(r"(?i)\b(sponsored|ad \d of \d|video will play after ad|ad will end in|advertisement)\b")


def media_window() -> dict[str, Any] | None:
    """The window the user is most likely watching: foreground browser/player, else any YouTube window."""
    from jarvis.tools.desktop import active_window, list_top_windows

    fg = active_window()
    if fg and fg["process"].lower() in BROWSERS + PLAYERS and "jarvis" not in fg["title"].lower():
        return fg
    wins = list_top_windows()
    for key in ("youtube", "netflix", "prime video", "hotstar", "twitch", "spotify", "vlc"):
        for w in wins:
            if key in w["title"].lower() and not w["minimized"]:
                return w
    for w in wins:
        if w["process"].lower() in BROWSERS + PLAYERS and not w["minimized"]:
            return w
    return None


def _find(hwnd: int, predicate: Any, limit: int = 3000) -> Any:
    import uiautomation as auto

    from jarvis.tools.communication import _uia_find

    root = auto.ControlFromHandle(hwnd)
    return _uia_find(root, predicate, max_depth=45, limit=limit)


def _is_skip(c: Any) -> bool:
    name = (c.Name or "").strip()
    return bool(name) and c.ControlTypeName in ("ButtonControl", "HyperlinkControl", "TextControl", "GroupControl") \
        and _SKIP.search(name) is not None and c.BoundingRectangle.width() > 0


def try_skip_ad(hwnd: int) -> str:
    """One attempt: 'skipped', 'waiting' (ad showing, not skippable yet) or 'none'."""
    import uiautomation as auto

    from jarvis.tools.communication import _click

    with auto.UIAutomationInitializerInThread():
        btn = _find(hwnd, _is_skip)
        if btn is not None:
            _click(btn)
            return "skipped"
        ad = _find(hwnd, lambda c: _AD_HINT.search(c.Name or "") is not None, limit=2000)
        return "waiting" if ad is not None else "none"


@tool("skip_ad", "Skip the ad in the video the user is watching (YouTube etc.). Waits for the Skip button if the "
      "ad isn't skippable yet.", {"wait_seconds": {"type": "integer"}}, category="execute", group="live", timeout=45)
def skip_ad(ctx: ToolContext, wait_seconds: int = 12) -> ToolResult:
    win = media_window()
    if not win:
        raise ToolError("I don't see a video or browser window open")
    end = time.time() + max(1, min(wait_seconds, 30))
    state = "none"
    while time.time() < end:
        ctx.check()
        state = try_skip_ad(win["hwnd"])
        if state == "skipped":
            time.sleep(1.2)
            again = try_skip_ad(win["hwnd"])  # verify the ad is gone (or skip the 2nd ad of a pair)
            note = " (and the second ad)" if again == "skipped" else ""
            return ToolResult(True, output=f"Skipped the ad{note} in '{win['title'][:60]}'.", data={"verified": True})
        if state == "none":
            break
        ctx.cancel.wait(1.0)  # countdown still running
    # OCR fallback for players without accessibility labels
    from jarvis.services.screen import ScreenService

    left, top, right, bottom = win["rect"]
    match = ScreenService.find_text("Skip", region=(left, top, right, bottom))
    if match:
        import pyautogui

        pyautogui.click(*match["center"])
        return ToolResult(True, output=f"Clicked '{match['text']}' to skip the ad.", data={"via": "ocr"})
    if state == "waiting":
        return ToolResult(False, error="there's an ad playing but it can't be skipped yet (or it's unskippable)")
    return ToolResult(False, error="no ad is playing right now - nothing to skip")


_YT_KEYS = {"play_pause": "k", "mute": "m", "fullscreen": "f", "captions": "c", "forward": "l", "back": "j",
            "next": "shift+n", "previous": "shift+p", "faster": "shift+.", "slower": "shift+,", "theater": "t"}
_GENERIC_KEYS = {"play_pause": "space", "mute": "m", "fullscreen": "f", "forward": "right", "back": "left"}
_MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1}


@tool("video_control", "Control the video/music the user is watching right now: play_pause, mute, fullscreen, "
      "captions, forward (10s), back (10s), next, previous, faster, slower, theater.",
      {"action": {"type": "string", "enum": sorted(_YT_KEYS)}, "times": {"type": "integer"}},
      required=["action"], category="execute", group="live", timeout=20)
def video_control(ctx: ToolContext, action: str, times: int = 1) -> ToolResult:
    import pyautogui

    from jarvis.tools.desktop import _vk, focus_hwnd

    win = media_window()
    if win is None or win["process"].lower() in PLAYERS and action in _MEDIA_KEYS:
        if action in _MEDIA_KEYS:
            for _ in range(max(1, times)):
                _vk(_MEDIA_KEYS[action])
            return ToolResult(True, output=f"sent {action.replace('_', '/')} to the media player")
        raise ToolError("I don't see a video playing")
    title = win["title"].lower()
    keys = _YT_KEYS if "youtube" in title else {**_GENERIC_KEYS, **({k: v for k, v in _YT_KEYS.items()
                                                                    if k in ("captions",)})}
    combo = keys.get(action)
    if combo is None:
        if action in _MEDIA_KEYS:
            _vk(_MEDIA_KEYS[action])
            return ToolResult(True, output=f"sent {action} media key")
        raise ToolError(f"'{action}' isn't supported for this player")
    if not focus_hwnd(win["hwnd"]):
        raise ToolError(f"couldn't bring '{win['title'][:50]}' to the front")
    time.sleep(0.15)
    for _ in range(max(1, min(times, 20))):
        parts = combo.split("+")
        pyautogui.hotkey(*parts) if len(parts) > 1 else pyautogui.press(parts[0])
        time.sleep(0.08)
    return ToolResult(True, output=f"{action.replace('_', '/')} on '{win['title'][:60]}'")


@tool("set_auto_skip_ads", "Turn automatic ad skipping on/off: JARVIS watches the video and clicks Skip as soon as "
      "it appears.", {"on": {"type": "boolean"}}, required=["on"], category="execute", group="live")
def set_auto_skip_ads(ctx: ToolContext, on: bool) -> str:
    ctx.app.config.set("live.auto_skip_ads", bool(on))
    AdSkipWatcher.ensure(ctx.app)
    return "Auto ad-skip is on - I'll skip ads as soon as they can be skipped." if on else "Auto ad-skip is off."


@tool("click_on_screen", "Click a button, link, tab or text that's visible on screen by its label "
      "(e.g. 'Subscribe', 'Accept all', 'Next'). Uses the accessibility tree, then OCR.",
      {"label": {"type": "string"}, "double": {"type": "boolean"}},
      required=["label"], category="execute", group="live", timeout=30)
def click_on_screen(ctx: ToolContext, label: str, double: bool = False) -> ToolResult:
    import pyautogui
    import uiautomation as auto

    from jarvis.tools.communication import _click
    from jarvis.tools.desktop import active_window

    win = active_window()
    if win and "jarvis" in win["title"].lower():
        win = media_window() or win
    target = label.lower().strip()
    if win:
        with auto.UIAutomationInitializerInThread():
            exact = _find(win["hwnd"], lambda c: (c.Name or "").strip().lower() == target
                          and c.ControlTypeName in ("ButtonControl", "HyperlinkControl", "MenuItemControl",
                                                    "TabItemControl", "CheckBoxControl", "ListItemControl")
                          and c.BoundingRectangle.width() > 0)
            ctrl = exact or _find(win["hwnd"], lambda c: target in (c.Name or "").lower()
                                  and c.ControlTypeName in ("ButtonControl", "HyperlinkControl", "MenuItemControl",
                                                            "TabItemControl") and c.BoundingRectangle.width() > 0)
            if ctrl is not None:
                if double:
                    r = ctrl.BoundingRectangle
                    pyautogui.doubleClick((r.left + r.right) // 2, (r.top + r.bottom) // 2)
                else:
                    _click(ctrl)
                return ToolResult(True, output=f"clicked '{ctrl.Name}' in '{win['title'][:50]}'")
    from jarvis.services.screen import ScreenService

    match = ScreenService.find_text(label)
    if match:
        pyautogui.click(*match["center"], clicks=2 if double else 1, interval=0.08)
        return ToolResult(True, output=f"clicked '{match['text']}' (found by reading the screen)")
    raise ToolError(f"I can't see anything labelled '{label}' on screen")


def youtube_lookup(query: str) -> tuple[str, str]:
    """First YouTube result for a search: (video_id, title). No API key needed."""
    import html as _html
    from urllib.parse import quote_plus

    import httpx

    r = httpx.get("https://www.youtube.com/results?search_query=" + quote_plus(query),
                  headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                         "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
                           "Accept-Language": "en-US,en;q=0.9"}, timeout=15, follow_redirects=True)
    r.raise_for_status()
    for m in re.finditer(r'"videoRenderer":\{"videoId":"([\w-]{11})".{0,1500}?"title":\{"runs":\[\{"text":"(.*?)"\}',
                         r.text, re.S):
        import json as _json

        try:
            title = _json.loads('"' + m.group(2) + '"')
        except ValueError:
            title = m.group(2)
        return m.group(1), _html.unescape(title)
    m = re.search(r'"videoId":"([\w-]{11})"', r.text)
    if not m:
        raise ToolError(f"YouTube returned no results for '{query}'")
    return m.group(1), query


@tool("play_media", "Play a song, video, playlist or podcast by name: finds it on YouTube (or YouTube Music) and "
      "starts it in the browser. Use for 'play X', 'put on some lofi', 'play the latest MKBHD video'.",
      {"query": {"type": "string"}, "service": {"type": "string", "enum": ["youtube", "youtube_music"]}},
      required=["query"], category="execute", group="live", timeout=45)
def play_media(ctx: ToolContext, query: str, service: str = "youtube") -> ToolResult:
    vid, title = youtube_lookup(query)
    host = "music.youtube.com" if service == "youtube_music" else "www.youtube.com"
    webbrowser.open(f"https://{host}/watch?v={vid}")
    end = time.time() + 15
    from jarvis.tools.desktop import list_top_windows

    while time.time() < end:
        if ctx.cancel.wait(0.7):
            break
        for w in list_top_windows():
            if "youtube" in w["title"].lower() and w["process"].lower() in BROWSERS:
                return ToolResult(True, output=f"Playing “{title}” on YouTube.",
                                  data={"video_id": vid, "title": title, "window": w["title"], "verified": True})
    return ToolResult(True, output=f"Opened “{title}” on YouTube (couldn't confirm the tab yet).",
                      data={"video_id": vid, "title": title, "verified": False})


class AdSkipWatcher:
    """Background watcher that skips ads while 'auto skip ads' is on."""

    _instance: AdSkipWatcher | None = None

    def __init__(self, app: Jarvis) -> None:
        self.app = app
        self._thread = threading.Thread(target=self._loop, name="jarvis-adskip", daemon=True)
        self._thread.start()

    @classmethod
    def ensure(cls, app: Jarvis) -> None:
        if cls._instance is None or not cls._instance._thread.is_alive():
            cls._instance = cls(app)

    def _loop(self) -> None:
        while self.app.config.get("live.auto_skip_ads", False):
            try:
                win = media_window()
                if win and any(k in win["title"].lower() for k in ("youtube", "twitch", "hotstar")):
                    if try_skip_ad(win["hwnd"]) == "skipped":
                        log.info("auto-skipped an ad in %s", win["title"][:60])
                        self.app.events.publish("live.ad_skipped", window=win["title"][:80])
            except Exception:  # noqa: BLE001 - keep watching
                log.debug("ad watcher tick failed", exc_info=True)
            time.sleep(1.5)
        AdSkipWatcher._instance = None
