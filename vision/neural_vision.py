"""Neural Visual Grounding and Screen Element Locator for JARVIS.

Analyzes OCR bounding boxes and UI text elements to ground conversational
targets (e.g. "click submit", "find search bar") into concrete screen coordinates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from vision.analyze import analyze_screen


@dataclass
class UIElement:
    text: str
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2)
    center_x: int
    center_y: int
    confidence: float
    kind: str  # button, text, input, tab, menu


class NeuralVisionGrounder:
    """Grounds user natural language requests to physical screen coordinates."""

    BUTTON_KEYWORDS = {"submit", "ok", "cancel", "save", "open", "close", "search", "send", "run", "start", "stop", "apply"}

    def scan_elements(self) -> list[UIElement]:
        """Capture screen and detect all localized UI elements with coordinates."""
        try:
            analysis = analyze_screen()
        except Exception as exc:
            print(f"[NEURAL_VISION] Screen capture failed: {exc}")
            return []

        elements: list[UIElement] = []
        for box in getattr(analysis, "boxes", []):
            text = (box.text or "").strip()
            if not text:
                continue

            x1, y1, x2, y2 = box.bbox
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2

            lower = text.lower()
            kind = "text"
            if any(k in lower for k in self.BUTTON_KEYWORDS) or len(text.split()) <= 2:
                kind = "button"

            elements.append(
                UIElement(
                    text=text,
                    bbox=(x1, y1, x2, y2),
                    center_x=cx,
                    center_y=cy,
                    confidence=0.9,
                    kind=kind,
                )
            )

        return elements

    def find_target(self, target_phrase: str) -> tuple[int, int] | None:
        """Find the center (x, y) coordinates of a target element on screen."""
        clean_target = re.sub(r"^(?:click|tap|press|select|find)\s+(?:on\s+)?(?:the\s+)?", "", target_phrase.lower()).strip()
        if not clean_target:
            return None

        elements = self.scan_elements()
        if not elements:
            return None

        # 1. Exact match
        for el in elements:
            if clean_target == el.text.lower():
                return (el.center_x, el.center_y)

        # 2. Substring match
        for el in elements:
            if clean_target in el.text.lower() or el.text.lower() in clean_target:
                return (el.center_x, el.center_y)

        # 3. Token overlap match
        target_tokens = set(clean_target.split())
        best_match = None
        best_overlap = 0

        for el in elements:
            el_tokens = set(el.text.lower().split())
            overlap = len(target_tokens.intersection(el_tokens))
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = el

        if best_match and best_overlap > 0:
            return (best_match.center_x, best_match.center_y)

        return None


_vision_grounder: NeuralVisionGrounder | None = None


def get_vision_grounder() -> NeuralVisionGrounder:
    global _vision_grounder
    if _vision_grounder is None:
        _vision_grounder = NeuralVisionGrounder()
    return _vision_grounder
