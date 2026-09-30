"""Tests for brain module."""

import pytest
from unittest.mock import Mock, patch, MagicMock

from core.brain import Brain, BrainError


class TestBrain:
    """Test Brain class."""

    def test_initialization(self):
        brain = Brain()
        assert brain is not None
        assert hasattr(brain, 'model')
        assert hasattr(brain, 'respond')
        assert hasattr(brain, 'respond_stream')
        assert hasattr(brain, 'respond_fast')
        assert hasattr(brain, 'warm_up')

    def test_respond_returns_string(self):
        brain = Brain()
        with patch.object(brain, '_jarvis') as mock_jarvis:
            mock_jarvis.ask.return_value = "Test response"
            response = brain.respond("Hello")
            assert isinstance(response, str)
            assert response == "Test response"

    def test_respond_handles_error(self):
        brain = Brain()
        with patch.object(brain, '_jarvis') as mock_jarvis:
            mock_jarvis.ask.side_effect = Exception("Model error")
            with pytest.raises(BrainError):
                brain.respond("Hello")

    def test_respond_fast(self):
        brain = Brain()
        with patch.object(brain, '_jarvis') as mock_jarvis:
            mock_jarvis.ask.return_value = "Fast response"
            response = brain.respond_fast("What is 2+2?")
            assert response == "Fast response"

    def test_respond_stream(self):
        brain = Brain()
        with patch.object(brain, '_jarvis') as mock_jarvis:
            mock_jarvis.ask_stream.return_value = iter(["Hello", " world", "!"])
            chunks = list(brain.respond_stream("Hello"))
            assert chunks == ["Hello", " world", "!"]

    def test_warm_up(self):
        brain = Brain()
        with patch.object(brain, '_jarvis') as mock_jarvis:
            mock_jarvis.ask.return_value = "Warmed up"
            # Should not raise
            brain.warm_up()

    def test_model_property(self):
        brain = Brain()
        assert brain.model == "llama3.2"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])