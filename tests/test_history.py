"""Tests for history module."""

import pytest
import tempfile
from pathlib import Path

from core.history import HistoryStore


class TestHistoryStore:
    """Test HistoryStore class."""

    def test_initialization(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.json"
            history = HistoryStore(path)
            assert history.path == path

    def test_append_and_get(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.json"
            history = HistoryStore(path)
            
            history.append("user", "Hello")
            history.append("assistant", "Hi there!")
            
            entries = history.get()
            assert len(entries) == 2
            assert entries[0]["role"] == "user"
            assert entries[0]["content"] == "Hello"
            assert entries[1]["role"] == "assistant"
            assert entries[1]["content"] == "Hi there!"

    def test_get_with_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.json"
            history = HistoryStore(path)
            
            for i in range(10):
                history.append("user", f"Message {i}")
            
            entries = history.get(limit=5)
            assert len(entries) == 5
            assert entries[0]["content"] == "Message 5"

    def test_clear(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.json"
            history = HistoryStore(path)
            
            history.append("user", "Hello")
            history.clear()
            
            entries = history.get()
            assert len(entries) == 0

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.json"
            history = HistoryStore(path)
            history.append("user", "Test")
            
            # Reload
            history2 = HistoryStore(path)
            entries = history2.get()
            assert len(entries) == 1
            assert entries[0]["content"] == "Test"

    def test_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.json"
            history = HistoryStore(path)
            history.append("user", "Hello")
            history.append("assistant", "Hi")
            
            snap = history.snapshot()
            assert len(snap) == 2
            assert snap[0]["role"] == "user"
            assert snap[0]["content"] == "Hello"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])