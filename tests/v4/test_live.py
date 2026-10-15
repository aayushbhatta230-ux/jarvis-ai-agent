"""Real-time commands: routing to direct actions and the live tools' behaviour (UI calls mocked)."""

from __future__ import annotations

import pytest

from jarvis.app import quick_action


@pytest.mark.parametrize("text,tool,args", [
    ("Jarvis, skip the ad", "skip_ad", {}),
    ("can you skip this ad please", "skip_ad", {}),
    ("pause the video", "video_control", {"action": "play_pause"}),
    ("play", "video_control", {"action": "play_pause"}),
    ("mute", "video_control", {"action": "mute"}),
    ("go full screen", "video_control", {"action": "fullscreen"}),
    ("next video", "video_control", {"action": "next"}),
    ("skip ahead 30 seconds", "video_control", {"action": "forward", "times": 3}),
    ("rewind", "video_control", {"action": "back", "times": 1}),
    ("turn on subtitles", "video_control", {"action": "captions"}),
    ("keep skipping ads for me", "set_auto_skip_ads", {"on": True}),
    ("stop skipping ads", "set_auto_skip_ads", {"on": False}),
    ("set the volume to 30", "set_volume", {"level": 30}),
    ("volume up", "media_control", {"action": "volume_up"}),
    ("scroll down", "scroll", {"amount": -5}),
    ("click the subscribe button", "click_on_screen", {"label": "subscribe"}),
    ("press accept all", "click_on_screen", {"label": "accept all"}),
])
def test_real_time_commands_map_to_direct_actions(text, tool, args):
    assert quick_action(text) == (tool, args)


@pytest.mark.parametrize("text", ["what's the weather like", "play some relaxing music from my playlist on spotify "
                                  "and then open notepad", "read my screen and text aayush", "how are you"])
def test_longer_or_other_requests_go_elsewhere(text):
    assert quick_action(text) is None


def test_direct_action_reports_honestly(app, monkeypatch):
    from jarvis.tools import live

    msgs = []
    app.events.subscribe("assistant.message", lambda e: msgs.append(e.data["text"]))
    monkeypatch.setattr(live, "media_window", lambda: None)
    app.handle_input("skip the ad", "text")
    assert msgs[-1].startswith("Couldn't do that") and "video" in msgs[-1]


def test_skip_ad_clicks_and_verifies(app, monkeypatch):
    from jarvis.tools import live

    states = iter(["waiting", "skipped", "none"])
    monkeypatch.setattr(live, "media_window", lambda: {"hwnd": 1, "title": "Song - YouTube", "process": "chrome.exe",
                                                       "rect": (0, 0, 100, 100), "minimized": False})
    monkeypatch.setattr(live, "try_skip_ad", lambda hwnd: next(states))
    monkeypatch.setattr(live.time, "sleep", lambda s: None)
    r = app.tools.execute("skip_ad", {"wait_seconds": 5})
    assert r.ok and "Skipped the ad" in r.output


def test_no_ad_means_nothing_to_skip(app, monkeypatch):
    from jarvis.services import screen
    from jarvis.tools import live

    monkeypatch.setattr(live, "media_window", lambda: {"hwnd": 1, "title": "x - YouTube", "process": "chrome.exe",
                                                       "rect": (0, 0, 100, 100), "minimized": False})
    monkeypatch.setattr(live, "try_skip_ad", lambda hwnd: "none")
    monkeypatch.setattr(screen.ScreenService, "find_text", staticmethod(lambda *a, **k: None))
    r = app.tools.execute("skip_ad", {})
    assert not r.ok and "no ad" in r.error


def test_auto_skip_toggle_persists(app, monkeypatch):
    from jarvis.tools import live

    monkeypatch.setattr(live.AdSkipWatcher, "ensure", classmethod(lambda cls, a: None))
    assert app.tools.execute("set_auto_skip_ads", {"on": True}).ok
    assert app.config.get("live.auto_skip_ads") is True
    app.tools.execute("set_auto_skip_ads", {"on": False})
    assert app.config.get("live.auto_skip_ads") is False
