"""Comprehensive tests for Neural Vector Memory, GPT Engine, Neural Intent Router,
System Diagnostics, Smart Notes, and Continuous Learning.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest
import numpy as np

from core.neural_memory import NeuralMemory, NeuralSubwordProjector
from core.neural_intent import NeuralIntentRouter
from core.gpt_engine import GPTEngine
from tools.system_diagnostics import get_system_diagnostics, format_diagnostics_speech
from tools.smart_notes import SmartNotesManager
from core.continuous_learning import ContinuousLearner


def test_neural_subword_projector():
    projector = NeuralSubwordProjector(dim=384)
    v1 = projector.encode("Python machine learning and neural networks")
    v2 = projector.encode("Deep learning artificial intelligence algorithms")
    v3 = projector.encode("Cooking pasta with tomato sauce")

    assert v1.shape == (384,)
    assert v2.shape == (384,)
    assert v3.shape == (384,)

    # Vectors should be unit normalized
    assert np.isclose(float(np.linalg.norm(v1)), 1.0, atol=1e-3)

    sim_ai = float(np.dot(v1, v2))
    sim_unrelated = float(np.dot(v1, v3))

    assert sim_ai > sim_unrelated


def test_neural_vector_memory():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_memory.db"
        mem = NeuralMemory(db_path=db_path)

        # Store test vectors
        mem.store("User is developing JARVIS autonomous assistant", category="project", importance=1.5)
        mem.store("User prefers Python and clean vanilla CSS", category="preference", importance=1.2)
        mem.store("Local model runs on Ollama with LLaMA 3.2", category="system", importance=1.0)

        stats = mem.get_stats()
        assert stats["total_vectors"] == 3
        assert "project" in stats["categories"]

        # Test semantic search
        results = mem.search("what code language does the user prefer?", top_k=2)
        assert len(results) > 0
        assert any("Python" in r["content"] for r in results)

        # Test RAG context generation
        rag = mem.get_rag_context("tell me about the user project")
        assert "RELEVANT NEURAL MEMORIES:" in rag
        assert "JARVIS" in rag

        # Test auto-learning
        learned = mem.auto_learn("My project is called IronMan and remember that I sleep at 2 AM")
        assert len(learned) >= 2


def test_neural_intent_router():
    router = NeuralIntentRouter()

    # Diagnostics
    intent, score, _ = router.classify("how is my battery and cpu doing?")
    assert intent == "system_diagnostics"
    assert score > 0.20

    # Cache clean
    intent, score, _ = router.classify("flush system temp cache now")
    assert intent == "clean_cache"
    assert score > 0.30

    # Recent downloads
    intent, score, _ = router.classify("what was the recent download file")
    assert intent == "recent_downloads"
    assert score > 0.40

    # Notes
    intent, score, entities = router.classify("take a note remember to commit changes")
    assert intent == "smart_notes"
    assert "note_text" in entities


def test_gpt_engine_configuration():
    engine = GPTEngine()
    assert engine.active_provider in ("Local Ollama LLaMA", "OpenAI Cloud GPT")
    assert engine.active_model is not None


def test_system_diagnostics():
    diag = get_system_diagnostics()
    assert "cpu" in diag
    assert "memory" in diag
    assert "disk" in diag
    assert "power" in diag

    speech = format_diagnostics_speech(diag)
    assert "Master," in speech
    assert "%" in speech


def test_smart_notes():
    with tempfile.TemporaryDirectory() as tmpdir:
        notes_file = Path(tmpdir) / "notes.json"
        nm = SmartNotesManager(file_path=notes_file)

        note = nm.add_note("Research transformer attention mechanisms", title="Research")
        assert note["title"] == "Research"

        listed = nm.list_notes()
        assert len(listed) == 1

        found = nm.search_notes("neural attention mechanisms")
        assert len(found) > 0

        deleted = nm.delete_note(note["id"])
        assert deleted is True
        assert len(nm.list_notes()) == 0


def test_continuous_learner():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        learner = ContinuousLearner(state_path=state_file)

        learner.record_feedback(True, context="Great job finding recent downloads")
        report = learner.get_report()
        assert report["reinforcement_events"] == 1
        assert report["positive_feedback_ratio"] == 1.0

        cycle_res = learner.run_consolidation_cycle()
        assert cycle_res["cycle"] == 1
