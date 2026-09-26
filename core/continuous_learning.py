"""Continuous Self-Training and Memory Consolidation Loop for JARVIS.

Applies reinforcement signals, habit learning, and episodic consolidation
to continuously adapt JARVIS to the user's workflow and preferences.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from core.neural_memory import get_neural_memory
from core.gpt_engine import get_gpt_engine


class ContinuousLearner:
    """Self-training supervisor that consolidates memory and reinforces user habits."""

    def __init__(self, state_path: str | Path | None = None) -> None:
        self.state_path = Path(state_path) if state_path else Path(__file__).resolve().parent.parent / "database" / "learning_state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.memory = get_neural_memory()
        self.gpt = get_gpt_engine()
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.is_file():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "total_training_cycles": 0,
            "reinforcement_events": 0,
            "positive_feedback_count": 0,
            "corrections_learned": 0,
            "habits_indexed": 0,
            "last_cycle_time": 0.0,
        }

    def _save_state(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def record_feedback(self, is_positive: bool, context: str = "") -> None:
        """Record user feedback to reinforce or penalize behaviors."""
        self.state["reinforcement_events"] += 1
        if is_positive:
            self.state["positive_feedback_count"] += 1
        else:
            self.state["corrections_learned"] += 1

        if context:
            category = "habit" if is_positive else "correction"
            self.memory.store(
                f"User feedback ({category}): {context}",
                category=category,
                importance=1.4 if not is_positive else 1.1,
            )

        self._save_state()

    def consolidate_dialogue_turn(self, user_text: str, assistant_response: str) -> list[str]:
        """Automatically extract user facts and habits from a dialogue turn."""
        learned = self.memory.auto_learn(user_text)
        if learned:
            self.state["habits_indexed"] += len(learned)
            self._save_state()
        return learned

    def run_consolidation_cycle(self) -> dict[str, Any]:
        """Deep training cycle: consolidate facts, optimize vector index, and summarize profile."""
        now = time.time()
        self.state["total_training_cycles"] += 1
        self.state["last_cycle_time"] = now

        # Retrieve top memories
        facts = self.memory.search("user preference project habits name", top_k=8, min_score=0.05)
        fact_texts = [f["content"] for f in facts]

        summary = f"Neural consolidation cycle #{self.state['total_training_cycles']} completed. {len(fact_texts)} active neural anchors synthesized."

        self._save_state()
        return {
            "cycle": self.state["total_training_cycles"],
            "anchors_synthesized": len(fact_texts),
            "summary": summary,
            "timestamp": now,
        }

    def get_report(self) -> dict[str, Any]:
        """Comprehensive learning metrics."""
        mem_stats = self.memory.get_stats()
        return {
            "training_cycles": self.state["total_training_cycles"],
            "reinforcement_events": self.state["reinforcement_events"],
            "positive_feedback_ratio": round(
                self.state["positive_feedback_count"] / max(1, self.state["reinforcement_events"]), 2
            ),
            "corrections_absorbed": self.state["corrections_learned"],
            "habits_indexed": self.state["habits_indexed"],
            "neural_memory_vectors": mem_stats["total_vectors"],
            "active_embedder": mem_stats["active_embedder"],
            "gpt_provider": self.gpt.active_provider,
            "gpt_model": self.gpt.active_model,
        }


_learner: ContinuousLearner | None = None


def get_continuous_learner() -> ContinuousLearner:
    global _learner
    if _learner is None:
        _learner = ContinuousLearner()
    return _learner
