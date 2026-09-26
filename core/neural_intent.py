"""Neural Intent Router for JARVIS using Dense Vector Semantic Embeddings.

Classifies natural language user queries into executable actions via
cosine similarity matching against high-dimensional intent prototypes.
"""

from __future__ import annotations

import re
from typing import Any
import numpy as np

from core.neural_memory import get_neural_memory


class NeuralIntentRouter:
    """Cosine similarity intent classifier trained on conversational prototypes."""

    INTENT_PROTOTYPES: dict[str, list[str]] = {
        "system_diagnostics": [
            "how is the pc performing",
            "system health check",
            "cpu and ram usage",
            "check battery level and power",
            "computer diagnostic status",
            "hardware metrics and memory load",
            "how much ram is free right now",
            "system resources overview",
        ],
        "clean_cache": [
            "clean up system cache",
            "clear temporary files and cache",
            "flush temp directory and logs",
            "purge workspace cache and pycache",
            "optimize disk storage by cleaning temp",
            "clean cache now",
            "empty temporary storage",
        ],
        "recent_downloads": [
            "what did i download recently",
            "check my downloads folder",
            "show me recent downloaded files",
            "what was the latest download",
            "look for download files",
            "inspect recent downloads",
            "what was downloaded on my pc",
        ],
        "smart_notes": [
            "take a note for me",
            "write down a note",
            "save a new note",
            "what are my notes",
            "search my notes",
            "list all saved notes",
            "delete this note",
        ],
        "screen_inspection": [
            "what do you see on my screen",
            "read what's on the monitor",
            "describe the active window and display",
            "what application is open right now",
            "inspect current desktop screen",
            "read the text on my display",
        ],
        "media_control": [
            "play music",
            "pause the song",
            "resume playback",
            "skip to next track",
            "increase volume",
            "lower the sound",
            "mute system audio",
            "turn volume up",
        ],
        "neural_memory_recall": [
            "what do you know about me",
            "recall what i told you earlier",
            "what is my favorite programming language",
            "search neural memory",
            "what do you remember about my project",
            "what are my preferences",
        ],
        "proactive_briefing": [
            "give me a daily briefing",
            "system status and overview",
            "morning briefing jarvis",
            "summary of system and active tasks",
        ],
    }

    def __init__(self) -> None:
        self.memory = get_neural_memory()
        self.prototypes: dict[str, np.ndarray] = {}
        self._build_prototype_centroids()

    def _build_prototype_centroids(self) -> None:
        """Precompute normalized centroid embeddings for each intent cluster."""
        for intent_name, examples in self.INTENT_PROTOTYPES.items():
            vecs = [self.memory.embedder.embed(ex) for ex in examples]
            centroid = np.mean(vecs, axis=0)
            norm = float(np.linalg.norm(centroid))
            if norm > 1e-6:
                centroid /= norm
            self.prototypes[intent_name] = centroid

    def classify(self, text: str, threshold: float = 0.12) -> tuple[str | None, float, dict[str, Any]]:
        """Classify user query into best-matching intent and confidence score."""
        clean_text = text.strip()
        if not clean_text:
            return None, 0.0, {}

        q_vec = self.memory.embedder.embed(clean_text)
        best_intent = None
        best_score = -1.0

        for intent_name, centroid in self.prototypes.items():
            sim = float(np.dot(q_vec, centroid))
            if sim > best_score:
                best_score = sim
                best_intent = intent_name

        entities: dict[str, Any] = {}
        # Entity extraction
        if best_intent == "smart_notes":
            note_match = re.search(r"(?:take|write|save|add)\s+(?:a\s+)?note\s+(?:called\s+|titled\s+|about\s+|to\s+|that\s+)?(.+)", clean_text, re.IGNORECASE)
            if note_match and note_match.group(1):
                entities["note_text"] = note_match.group(1).strip()

        if best_intent == "neural_memory_recall":
            entities["query"] = clean_text

        if best_score >= threshold:
            return best_intent, round(best_score, 4), entities

        return None, round(best_score, 4), entities


# Global singleton instance
_neural_intent_router: NeuralIntentRouter | None = None


def get_neural_intent_router() -> NeuralIntentRouter:
    global _neural_intent_router
    if _neural_intent_router is None:
        _neural_intent_router = NeuralIntentRouter()
    return _neural_intent_router
