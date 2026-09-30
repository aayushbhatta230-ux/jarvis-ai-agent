"""Tests for conversation manager."""

import pytest
from unittest.mock import Mock, MagicMock, patch

from core.conversation import ConversationManager


class TestConversationManager:
    """Test ConversationManager class."""

    def test_initialization(self):
        brain = Mock()
        speaker = Mock()
        listener = Mock()
        events = Mock()
        settings = Mock()
        history = Mock()
        
        manager = ConversationManager(
            brain=brain,
            speaker=speaker,
            listener=listener,
            events=events,
            settings=settings,
            history=history,
        )
        assert manager.brain is brain
        assert manager.speaker is speaker
        assert manager.listener is listener
        assert manager.events is events
        assert manager.settings is settings
        assert manager.history is history
        assert manager.state == "idle"
        assert manager.turn_id == 0

    def test_state_transitions(self):
        brain = Mock()
        speaker = Mock()
        events = Mock()
        
        manager = ConversationManager(brain=brain, speaker=speaker, events=events)
        
        # Test valid states
        for state in manager.VALID_STATES:
            manager.set_state(state)
            assert manager.state == state
        
        # Test invalid state raises
        with pytest.raises(ValueError):
            manager.set_state("invalid_state")

    def test_voice_response_truncation(self):
        brain = Mock()
        speaker = Mock()
        events = Mock()
        
        manager = ConversationManager(brain=brain, speaker=speaker, events=events)
        
        # Test single sentence
        result = manager._voice_response(
            "Hello there. How are you?",
            "general_request",
            "Hello"
        )
        assert result == "Hello there."
        
        # Test no period
        result = manager._voice_response(
            "Hello there",
            "general_request",
            "Hello"
        )
        assert result == "Hello there."

    def test_finish_request(self):
        brain = Mock()
        speaker = Mock()
        events = Mock()
        
        manager = ConversationManager(brain=brain, speaker=speaker, events=events)
        
        # Test with None (should not raise)
        manager._finish_request(None)
        
        # Test with request ID
        manager.active_request_id = "test-id"
        manager._finish_request("test-id")
        assert "test-id" in manager.completed_request_ids


if __name__ == "__main__":
    pytest.main([__file__, "-v"])