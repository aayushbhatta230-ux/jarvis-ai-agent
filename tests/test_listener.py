"""Tests for voice listener module."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import numpy as np

from voice.listener import Listener, TranscriptResult, _AudioCapture


class TestTranscriptResult:
    """Test TranscriptResult dataclass."""

    def test_creation(self):
        result = TranscriptResult(
            text="hello world",
            confidence=0.95,
            duration=1.5,
            audio_quality="good",
            speech_detected=True,
        )
        assert result.text == "hello world"
        assert result.confidence == 0.95
        assert result.duration == 1.5
        assert result.audio_quality == "good"
        assert result.speech_detected is True


class TestAudioCapture:
    """Test _AudioCapture singleton."""

    def test_singleton_pattern(self):
        capture1 = _AudioCapture()
        capture2 = _AudioCapture()
        assert capture1 is capture2

    def test_initialization(self):
        capture = _AudioCapture(sample_rate=16000, block_size=320)
        assert capture.sample_rate == 16000
        assert capture.block_size == 320

    @patch('voice.listener.sd.InputStream')
    def test_start_stop(self, mock_stream):
        mock_stream.return_value.__enter__ = Mock()
        mock_stream.return_value.__exit__ = Mock()
        
        capture = _AudioCapture()
        # Can't easily test threading in unit test, but verify methods exist
        assert hasattr(capture, 'start')
        assert hasattr(capture, 'stop')
        assert hasattr(capture, 'read_block')
        assert hasattr(capture, 'clear_buffer')


class TestListener:
    """Test Listener class."""

    def test_initialization(self):
        listener = Listener(sample_rate=16000, sensitivity=1.0)
        assert listener.sample_rate == 16000
        assert listener.sensitivity == 1.0

    def test_sensitivity_clamping(self):
        listener = Listener(sensitivity=3.0)
        assert listener.sensitivity == 2.5  # max
        
        listener = Listener(sensitivity=0.1)
        assert listener.sensitivity == 0.4  # min

    def test_close(self):
        listener = Listener()
        # Should not raise
        listener.close()

    def test_adaptive_threshold(self):
        listener = Listener(noise_floor=0.005, sensitivity=1.0)
        noise_levels = [0.01, 0.012, 0.009, 0.011]
        threshold = listener._adaptive_threshold(noise_levels, 0.015)
        # threshold should be above noise floor
        assert threshold >= listener.noise_floor

    def test_boosted_audio(self):
        audio = np.array([[[0.1]], [[0.2]], [[0.3]]], dtype=np.float32)
        boosted = Listener._boosted(audio)
        # Peak should be normalized to ~0.65
        assert np.max(np.abs(boosted)) <= 1.0
        assert np.max(np.abs(boosted)) > 0.5

    def test_as_pcm_bytes(self):
        audio = np.array([[[0.5]], [[-0.5]]], dtype=np.float32)
        pcm = Listener._as_pcm_bytes(audio)
        assert isinstance(pcm, bytes)
        assert len(pcm) == 4  # 2 samples * 2 bytes


if __name__ == "__main__":
    pytest.main([__file__, "-v"])