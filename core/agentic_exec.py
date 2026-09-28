"""
Agentic Plan Executor
=====================
Runs a validated plan from :mod:`core.agentic` against the real system.

Each step dispatches to the same vetted helpers the rest of JARVIS already
uses (``tools.remote_control``, ``core.screencontrol``,
``tools.file_authoring``), so agentic behaviour is not a second, less-tested
code path.

The executor is deliberately conservative:
* A step that cannot be performed reports *why* instead of silently degrading
  into a different action (the old "clicked at the cursor" bug).
* A failed click aborts the plan, so text never lands in the wrong window.
"""

from __future__ import annotations

import re
import time
from typing import Any

# Simple system/app actions that map 1:1 onto tools.remote_control verbs.
_REMOTE_MAP = {
    "minimize_window": "minimize_window",
    "maximize_window": "maximize_window",
    "restore_window": "restore_window",
    "close_window": "close_window",
    "show_desktop": "show_desktop",
    "lock_pc": "lock_pc",
    "sleep_pc": "sleep_pc",
    "volume_up": "volume_up",
    "volume_down": "volume_down",
    "mute": "mute",
    "play_pause": "play_pause",
    "next_track": "next_track",
    "prev_track": "prev_track",
    "screenshot": "screenshot",
}


def _run_remote(action: str, **kwargs: Any) -> dict[str, Any]:
    from tools.remote_control import execute_remote_action
    return execute_remote_action(action, **kwargs)


def _click_text(target: str, clicks: int = 1, button: str = "left") -> dict[str, Any]:
    """Click a UI element located by OCR, verifying it really resolved.

    People say "the file menu" when the screen simply reads "File". We try the
    phrase as given, then progressively simplify it ("file menu" -> "file") so
    natural phrasing still resolves to the real control.
    """
    from core.screencontrol import get_screen_control
    controller = get_screen_control()

    attempts = [target]
    simplified = re.sub(r"\s+(menu|button|btn|tab|icon|link|bar)$", "", target, flags=re.I).strip()
    simplified = re.sub(r"^(?:the|my|a|an)\s+", "", simplified, flags=re.I).strip()
    if simplified and simplified.lower() != target.lower():
        attempts.append(simplified)

    info = None
    for candidate in attempts:
        probe = controller.find_ui_target(candidate)
        if probe and probe.get("status") == "FOUND" and "center" in probe:
            info = probe
            break
        # Keep the first non-UNKNOWN result so we can report ambiguity.
        if info is None and probe:
            info = probe

    if not info or info.get("status") != "FOUND" or "center" not in info:
        if info and info.get("status") == "AMBIGUOUS":
            return {"ok": False,
                    "error": f"I found more than one '{target}' on screen. Which one did you mean?"}
        return {"ok": False, "error": f"I couldn't find '{target}' on your screen."}

    x, y = info["center"]
    result = controller.click_coordinates(x, y, button=button)
    if not getattr(result, "success", False):
        return {"ok": False, "error": getattr(result, "error", "The click didn't register.")}
    if clicks > 1:
        time.sleep(0.05)
        _run_remote("click", button=button, clicks=clicks - 1, x=x, y=y)
    return {"ok": True,
            "spoken": f"Clicked {info.get('text', target)}, sir."}


def execute_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Execute a sanitized plan.

    Returns ``spoken`` (what to say), optional ``display`` (a rich card), and
    ``needs_clarification`` when the user must answer first.
    """
    steps = plan.get("steps") or []
    spoken_parts: list[str] = []
    display: str | None = None
    question = plan.get("question") or ""

    for step in steps:
        action = step.get("action", "")

        if action == "needs_clarification":
            return {"spoken": question or "Could you say that again, sir?",
                    "needs_clarification": True}

        if action == "answer":
            # The model often appends a trailing "answer" step to say "and then
            # reply in words". Once real work has happened we must NOT delegate
            # (that would throw the completed actions away) -- just skip it.
            if not spoken_parts and display is None:
                return {"spoken": "", "delegate_to_llm": True}
            continue

        # ---------------- screen targeting ---------------- #
        if action in ("click_text", "double_click_text", "right_click_text"):
            res = _click_text(
                str(step.get("target", "")),
                clicks=2 if action == "double_click_text" else 1,
                button="right" if action == "right_click_text" else "left",
            )
            if not res.get("ok"):
                # Abort: continuing would type into the wrong place.
                return {"spoken": res.get("error", "I couldn't do that, sir."), "failed": True}
            spoken_parts.append(res.get("spoken") or "Done.")

        elif action == "click_xy":
            res = _run_remote("click", x=int(step["x"]), y=int(step["y"]))
            if not res.get("ok"):
                return {"spoken": res.get("error", "That click failed."), "failed": True}
            spoken_parts.append(f"Clicked at {step['x']}, {step['y']}, sir.")

        elif action == "move_mouse":
            res = _run_remote("move", x=int(step["x"]), y=int(step["y"]))
            if not res.get("ok"):
                return {"spoken": res.get("error", "Couldn't move the mouse."), "failed": True}
            spoken_parts.append("Moved the mouse, sir.")

        elif action == "type_text":
            res = _run_remote("type", text=str(step.get("text", "")), enter=False)
            if not res.get("ok"):
                return {"spoken": res.get("error", "I couldn't type that."), "failed": True}
            spoken_parts.append("Typed it, sir.")

        elif action == "press_keys":
            res = _press_keys(str(step.get("keys", "")))
            if not res.get("ok"):
                return {"spoken": res.get("error", "That key didn't work."), "failed": True}
            spoken_parts.append("Done.")

        elif action == "scroll":
            direction = str(step.get("direction", "down")).lower()
            res = _run_remote("scroll", direction=direction)
            if not res.get("ok"):
                return {"spoken": res.get("error", "Couldn't scroll."), "failed": True}
            spoken_parts.append(f"Scrolled {direction}, sir.")

        # ---------------- system & apps ---------------- #
        elif action == "switch_app":
            res = _run_remote("switch_window", name=str(step.get("app", "")))
            if not res.get("ok"):
                return {"spoken": res.get("error") or f"I couldn't switch to {step.get('app')}.",
                        "failed": True}
            spoken_parts.append(res.get("spoken") or f"Switched to {step.get('app')}.")

        elif action == "open_app":
            res = _run_remote("open_app", name=str(step.get("app", "")))
            if not res.get("ok"):
                return {"spoken": res.get("error") or f"I couldn't open {step.get('app')}.",
                        "failed": True}
            spoken_parts.append(res.get("spoken") or f"Opened {step.get('app')}.")

        elif action in _REMOTE_MAP:
            res = _run_remote(_REMOTE_MAP[action])
            if not res.get("ok"):
                return {"spoken": res.get("error", "That didn't work."), "failed": True}
            spoken_parts.append(res.get("spoken") or "Done.")


        # ---------------- files ---------------- #
        elif action == "create_file":
            from tools.file_authoring import create_file as do_create
            res = do_create(str(step.get("target", "")), str(step.get("text", "")))
            if not res.get("ok"):
                return {"spoken": res.get("error", "I couldn't create that file."), "failed": True}
            spoken_parts.append(res.get("spoken") or "Created the file.")
            display = res.get("markdown")

        elif action == "append_file":
            from tools.file_authoring import append_to_file as do_append
            res = do_append(str(step.get("target", "")), str(step.get("text", "")))
            if not res.get("ok"):
                return {"spoken": res.get("error", "I couldn't append to that file."), "failed": True}
            spoken_parts.append(res.get("spoken") or "Appended.")
            display = res.get("markdown")

        elif action in ("read_file", "summarize_file"):
            from tools.smart_files import inspect_and_read_file
            res = inspect_and_read_file(str(step.get("target", "")))
            if not res.get("ok"):
                return {"spoken": res.get("spoken") or "I couldn't read that file.", "failed": True}
            display = res.get("markdown") or res.get("display")
            spoken_parts.append(res.get("summary") or res.get("spoken") or "Here's what I found.")

        elif action == "list_files":
            from tools.smart_files import list_project_files
            res = list_project_files(step.get("target") or None)
            display = res.get("markdown") or res.get("display")
            spoken_parts.append(res.get("spoken") or "Here's the list.")

        elif action == "open_file":
            res = _run_remote("open_path", path=str(step.get("target", "")))
            spoken_parts.append(res.get("spoken") or "Opening it, sir.")

        elif action == "delete_file":
            from tools.file_authoring import delete_file as do_delete
            res = do_delete(str(step.get("target", "")))
            if not res.get("ok"):
                return {"spoken": res.get("error", "I couldn't delete that."), "failed": True}
            spoken_parts.append(res.get("spoken") or "Deleted.")

        else:
            continue  # unknown action; impossible after sanitisation

    reply = (plan.get("reply") or "").strip()
    spoken = reply or " ".join(spoken_parts) or "All done, sir."
    return {"spoken": spoken, "display": display}


__all__ = ["execute_plan", "_REMOTE_MAP"]
