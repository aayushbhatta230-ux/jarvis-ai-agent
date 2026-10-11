"""Screen-awareness tools exposed to the agent."""

from __future__ import annotations

import time
from typing import Any

from jarvis.services.screen import grab, to_b64_png
from jarvis.tools.registry import ToolContext, ToolError, ToolResult, tool


@tool("analyze_screen", "Look at the screen with a vision model (plus OCR) and answer a question about it: "
      "errors, dialogs, code, what the user is doing, where a button is, whether something succeeded.",
      {"question": {"type": "string"},
       "region": {"type": "string", "enum": ["screen", "window"], "description": "Whole screen or active window"}},
      required=["question"], group="screen", timeout=180)
def analyze_screen(ctx: ToolContext, question: str, region: str = "screen") -> dict[str, Any]:
    return ctx.app.screen.analyze(question, region=region, cancel=ctx.cancel)


@tool("read_screen_text", "OCR the active window (or whole screen) and return the visible text.",
      {"region": {"type": "string", "enum": ["screen", "window"]}}, group="screen", timeout=60)
def read_screen_text(ctx: ToolContext, region: str = "window") -> ToolResult:
    svc = ctx.app.screen
    win, wregion = svc.active_window_region()
    img = grab(wregion if region == "window" and wregion else None)
    text = svc.ocr(img)
    if not text.strip():
        return ToolResult(False, error="OCR found no text (or Tesseract is unavailable)")
    title = win["title"] if win else "?"
    return ToolResult(True, output=f"[{title}]\n{text[:10000]}", data={"window": title})


@tool("take_screenshot", "Capture the screen to a PNG file and attach it for a vision-capable model.",
      {"region": {"type": "string", "enum": ["screen", "window"]}}, group="screen")
def take_screenshot(ctx: ToolContext, region: str = "screen") -> ToolResult:
    svc = ctx.app.screen
    _, wregion = svc.active_window_region()
    img = grab(wregion if region == "window" and wregion else None)
    path = svc.save(img, "shot")
    return ToolResult(True, output=f"saved screenshot to {path} ({img.width}x{img.height})",
                      data={"path": str(path)}, images=[to_b64_png(img, 1024)])


@tool("find_text_on_screen", "Locate visible text on screen via OCR and return its screen coordinates.",
      {"text": {"type": "string"}}, required=["text"], group="screen", timeout=60)
def find_text_on_screen(ctx: ToolContext, text: str) -> dict[str, Any]:
    match = ctx.app.screen.find_text(text)
    if not match:
        raise ToolError(f"'{text}' was not found on screen")
    return match


@tool("wait_for_screen_text", "Wait until text appears on screen (verifying an action), up to timeout seconds.",
      {"text": {"type": "string"}, "timeout": {"type": "integer"}}, required=["text"], group="screen", timeout=200)
def wait_for_screen_text(ctx: ToolContext, text: str, timeout: int = 20) -> ToolResult:
    deadline = time.time() + max(2, min(timeout, 180))
    while time.time() < deadline:
        ctx.check()
        match = ctx.app.screen.find_text(text)
        if match:
            return ToolResult(True, output=f"'{text}' is visible at {match['center']}", data=match)
        ctx.cancel.wait(1.5)
    return ToolResult(False, error=f"'{text}' did not appear within {timeout}s")


@tool("screen_baseline", "Record the current screen as a baseline for a later before/after comparison.",
      {"label": {"type": "string"}}, group="screen", timeout=60)
def screen_baseline(ctx: ToolContext, label: str = "default") -> str:
    ctx.app.screen.baseline(label)
    return f"baseline '{label}' recorded"


@tool("screen_compare", "Compare the current screen with a recorded baseline: visual change and new text.",
      {"label": {"type": "string"}}, group="screen", timeout=60)
def screen_compare(ctx: ToolContext, label: str = "default") -> dict[str, Any]:
    try:
        return ctx.app.screen.compare(label)
    except KeyError as exc:
        raise ToolError(str(exc)) from exc


@tool("screen_context", "Get JARVIS's current awareness of the active window (title, process, OCR excerpt, "
      "visible errors).", group="screen", timeout=60)
def screen_context(ctx: ToolContext) -> str:
    return ctx.app.screen.refresh_context(force=True).summary(4000) or "screen context unavailable"
