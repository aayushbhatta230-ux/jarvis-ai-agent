"""
Agentic Action Planner
======================
Replaces brittle keyword/regex guessing with an LLM-driven planner.

The rest of JARVIS answers with long ``if "minimize" in text`` chains, which is
why it misfires (it once tried to "press win+down" for *"what is 2 plus 2"*).
This module asks the model for a small JSON plan, then executes each step
through the same vetted tools the rest of the system already uses.

Rules:
* The planner may only emit actions from an explicit allow-list.
* Unclear requests become ``needs_clarification`` with a question, not a guess.
* Multi-step requests ("click search, type cats, press enter") become ordered
  steps, so JARVIS acts like an operator rather than a keyword matcher.
* If the model is unavailable the caller keeps its existing behaviour, making
  this strictly an upgrade rather than a new failure mode.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Actions the planner is permitted to emit.
ALLOWED_ACTIONS = {
    "click_text": "Click the on-screen UI element whose visible text matches `target`.",
    "click_xy": "Click exact screen coordinates (x, y).",
    "double_click_text": "Double-click the on-screen element matching `target`.",
    "right_click_text": "Right-click the on-screen element matching `target`.",
    "move_mouse": "Move the mouse to coordinates (x, y).",
    "type_text": "Type a literal string into whatever is focused.",
    "press_keys": "Press a key or hotkey, e.g. 'enter', 'win+down', 'ctrl+s'.",
    "scroll": "Scroll the focused window (direction: up/down/left/right).",
    "minimize_window": "Minimize the currently focused window.",
    "maximize_window": "Maximize the currently focused window.",
    "restore_window": "Restore/un-maximize the focused window.",
    "close_window": "Close the focused window.",
    "switch_app": "Bring a named application to the foreground.",
    "open_app": "Launch a named application.",
    "show_desktop": "Show the desktop.",
    "lock_pc": "Lock the workstation.",
    "sleep_pc": "Put the PC to sleep.",
    "volume_up": "Raise system volume one step.",
    "volume_down": "Lower system volume one step.",
    "mute": "Toggle mute.",
    "play_pause": "Play/pause media.",
    "next_track": "Next track.",
    "prev_track": "Previous track.",
    "screenshot": "Capture the screen.",
    "create_file": "Create a file named `target` with the content in `text`.",
    "append_file": "Append `text` to the file named `target`.",
    "read_file": "Read the file named `target`.",
    "summarize_file": "Read `target` and return a short summary.",
    "list_files": "List files in a folder (`target`, optional).",
    "open_file": "Open a file in its default application.",
    "delete_file": "Delete the file named `target`.",
    "answer": "No computer action needed; just answer conversationally.",
    "needs_clarification": "The request is ambiguous; ask the user a question.",
}


_PLANNER_SYSTEM = """You are the action planner for JARVIS, a personal AI assistant that controls a Windows PC.

Convert the user's request into a JSON plan. Reply with ONLY a JSON object, no prose.

Schema:
{"steps": [{"action": "<action name>", ...args}], "reply": "<optional short spoken confirmation>", "question": "<only if asking>"}

Rules:
- Choose actions ONLY from this list:
{actions}
- Arguments: click_text/double_click_text/right_click_text -> "target" (the visible text to find);
  type_text -> "text"; press_keys -> "keys"; scroll -> "direction";
  create_file/append_file -> "target","text"; read_file/summarize_file/open_file/delete_file -> "target";
  switch_app/open_app -> "app"; click_xy/move_mouse -> "x","y".
- For pure conversation, arithmetic, opinions or general knowledge, use a single step with action "answer".
- If the request is genuinely ambiguous about what to do, use action "needs_clarification" and put the question in "question".
- Multi-step requests ("click search then type cats then press enter") MUST be ordered steps.
- Never invent file paths you were not given. Use "answer" if you lack the information.
- Keep "reply" to one short friendly sentence, or omit it.

Examples:
User: click on the search box
{{"steps":[{{"action":"click_text","target":"Search"}}],"reply":"Opening search."}}

User: what is 2 plus 2
{{"steps":[{{"action":"answer"}}]}}

User: create a file notes.txt with the content hello world
{{"steps":[{{"action":"create_file","target":"notes.txt","text":"hello world"}}],"reply":"Created notes.txt."}}
"""


def build_planner_prompt() -> str:
    listing = "\n".join(f"  - {name}: {desc}" for name, desc in ALLOWED_ACTIONS.items())
    return _PLANNER_SYSTEM.replace("{actions}", listing)


def _extract_json(raw: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model response."""
    if not raw:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def plan_from_text(text: str, brain: Any) -> dict[str, Any] | None:
    """Ask the model for a plan. Returns ``None`` if unavailable/unparseable.

    Callers must treat ``None`` as "fall back to existing behaviour".
    """
    if not text or brain is None:
        return None
    prompt = build_planner_prompt() + "\n\nUser request: " + text.strip() + "\n\nJSON:"
    # A generous budget: create_file steps must carry the full literal content,
    # and a tight limit silently truncated "hello from jarvis" to "hello from".
    try:
        raw = brain.respond_fast(prompt, tokens=600)
    except TypeError:
        try:
            raw = brain.respond_fast(prompt)
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001 - planner is best-effort
        return None
    return _extract_json(raw or "")


def sanitize_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate and normalise a raw plan against the allow-list."""
    if not isinstance(plan, dict):
        return None
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return None

    needs_target = {
        "click_text", "double_click_text", "right_click_text",
        "read_file", "summarize_file", "open_file", "delete_file",
        "create_file", "append_file",
    }
    clean: list[dict[str, Any]] = []
    for step in steps[:6]:  # hard cap; anything longer is a mis-parse
        if not isinstance(step, dict):
            continue
        action = str(step.get("action", "")).strip().lower()
        if action not in ALLOWED_ACTIONS:
            continue
        entry: dict[str, Any] = {"action": action}
        for key in ("target", "text", "keys", "direction", "app", "x", "y"):
            if key in step and step[key] not in (None, ""):
                entry[key] = step[key]
        if action in ("type_text", "create_file", "append_file") and "text" not in entry:
            continue  # nothing to type/write -> drop the step
        if action in needs_target and "target" not in entry:
            continue
        if action in ("switch_app", "open_app") and "app" not in entry:
            continue
        if action in ("click_xy", "move_mouse"):
            try:
                entry["x"], entry["y"] = int(entry["x"]), int(entry["y"])
            except (KeyError, TypeError, ValueError):
                continue
        clean.append(entry)

    if not clean:
        return None

    # A stray "needs_clarification" alongside real work is noise -- the model
    # hedging rather than actually being unsure. Keep it only when it is the
    # *sole* step, otherwise execute the concrete actions.
    if len(clean) > 1:
        clean = [s for s in clean if s["action"] != "needs_clarification"]
        if not clean:
            return None

    return {
        "steps": clean,
        "reply": str(plan.get("reply") or "").strip(),
        "question": str(plan.get("question") or "").strip(),
    }


__all__ = ["ALLOWED_ACTIONS", "plan_from_text", "sanitize_plan", "build_planner_prompt"]
