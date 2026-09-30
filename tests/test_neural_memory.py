"""Tests for neural memory module."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from core.neural_memory import NeuralMemory, NeuralEmbedder, NeuralSubwordProjector


class TestNeuralSubwordProjector:
    """Test NeuralSubwordProjector."""

    def test_initialization(self):
        projector = NeuralSubwordProjector(dim=384)
        assert projector.dim == 384

    def test_encode_empty(self):
        projector = NeuralSubwordProjector()
        vec = projector.encode("")
        assert len(vec) == 384
        assert all(v == 0 for v in vec)

    def test_encode_simple(self):
        projector = NeuralSubwordProjector()
        vec = projector.encode("hello world")
        assert len(vec) == 384
        # Should be normalized
        import numpy as np
        norm = np.linalg.norm(vec)
        assert abs(norm - 1.0) < 0.01

    def test_encode_consistency(self):
        projector = NeuralSubwordProjector()
        vec1 = projector.encode("hello")
        vec2 = projector.encode("hello")
        import numpy as np
        # Same input should produce same vector
        assert np.allclose(vec1, vec2)


class TestNeuralEmbedder:
    """Test NeuralEmbedder."""

    def test_initialization(self):
        embedder = NeuralEmbedder()
        assert embedder is not None
        assert hasattr(embedder, 'projector')

    def test_embed_empty(self):
        embedder = NeuralEmbedder()
        vec = embedder.embed("")
        assert len(vec) == 384
        import numpy as np
        assert np.allclose(vec, 0)

    def test_embed_caching(self):
        embedder = NeuralEmbedder()
        vec1 = embedder.embed("test text")
        vec2 = embedder.embed("test text")
        import numpy as np
        assert np.allclose(vec1, vec2)

    def test_normalize_dim(self):
        embedder = NeuralEmbedder()
        import numpy as np
        # Test with wrong dimension
        arr = np.random.rand(100).astype(np.float32)
        normalized = embedder._normalize_dim(arr)
        assert len(normalized) == 384
        
        # Test with larger dimension
        arr = np.random.rand(1000).astype(np.float32)
        normalized = embedder._normalize_dim(arr)
        assert len(normalized) == 384


class TestNeuralMemory:
    """Test NeuralMemory."""

    def test_initialization(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "memory.db"
            memory = NeuralMemory(path)
            assert memory is not None
            assert memory.db_path == path

    def test_store_and_search(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "memory.db"
            memory = NeuralMemory(path)
            
            # Store a fact
            memory.store("User likes Python", category="preference", importance=1.5)
            
            # Search for it
            results = memory.search("Python", top_k=5)
            assert len(results) > 0
            assert "Python" in results[0]["content"]

    def test_search_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "memory.db"
            memory = NeuralMemory(path)
            
            results = memory.search("nonexistent", top_k=5)
            assert results == []

    def test_auto_learn(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "memory.db"
            memory = NeuralMemory(path)
            
            learned = memory.auto_learn("My name is Alice")
            assert len(learned) > 0
            assert any("Alice" in fact for fact in learned)

    def test_get_rag_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "memory.db"
            memory = NeuralMemory(path)
            
            memory.store("User prefers dark mode", category="preference")
            memory.store("Project is called Jarvis", category="project")
            
            context = memory.get_rag_context("preferences", max_items=3)
            assert "dark mode" in context
            assert "Jarvis" in context

    def test_get_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "memory.db"
            memory = NeuralMemory(path)
            
            memory.store("Fact 1", category="fact")
            memory.store("Fact 2", category="fact")
            memory.store("Preference 1", category="preference")
            
            stats = memory.get_stats()
            assert stats["total_vectors"] >= 3
            assert "fact" in stats["categories"]
            assert "preference" in stats["categories"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])