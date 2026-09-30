"""Tests for speaker module."""

import pytest
from unittest.mock import Mock, MagicMock, patch
from queue import Queue, Empty

from voice.speaker import Speaker, SpeakerError


class TestSpeaker:
    """Test Speaker class."""

    def test_initialization(self):
        speaker = Speaker()
        assert speaker is not None
        assert speaker.voice_name is not None
        assert hasattr(speaker, 'speak')
        assert hasattr(speaker, 'enqueue_chunk')
        assert hasattr(speaker, 'stop_speaking')

    def test_voice_listing(self):
        speaker = Speaker()
        voices = speaker.list_voices()
        assert isinstance(voices, list)

    def test_speak_basic(self):
        speaker = Speaker()
        # Mock the engine
        with patch.object(speaker, '_engine') as mock_engine:
            mock_engine.Speak = Mock()
            mock_engine.WaitUntilDone = Mock()
            speaker.speak("Hello world", wait=True)
            mock_engine.Speak.assert_called()

    def test_enqueue_chunk(self):
        speaker = Speaker()
        speaker.enqueue_chunk("Hello")
        speaker.enqueue_chunk(" world")
        
        # Check queue
        assert speaker.queue.qsize() == 2

    def test_stop_speaking(self):
        speaker = Speaker()
        speaker.enqueue_chunk("Hello")
        speaker.stop_speaking()
        
        # Queue should be cleared
        assert speaker.queue.empty()
        assert speaker.cancel_event.is_set()

    def test_begin_utterance(self):
        speaker = Speaker()
        speaker.cancel_event.set()
        speaker.speaking.set()
        
        speaker.begin_utterance()
        
        assert not speaker.cancel_event.is_set()
        assert not speaker.speaking.is_set()
        assert speaker.generation == 1

    def test_muted_property(self):
        speaker = Speaker()
        assert speaker.muted is False
        
        speaker.muted = True
        assert speaker.muted is True
        
        speaker.muted = False
        assert speaker.muted is False

    def test_wait_until_idle(self):
        speaker = Speaker()
        # Should return quickly when idle
        speaker.wait_until_idle(timeout=1.0)

    def test_stop(self):
        speaker = Speaker()
        # Should not raise
        speaker.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])