"""Tests for memory module."""

import pytest
import tempfile
from pathlib import Path

from core.memory import PreferenceStore, ShortTermMemory


class TestPreferenceStore:
    """Test PreferenceStore class."""

    def test_initialization(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "prefs.json"
            store = PreferenceStore(path)
            assert store is not None

    def test_get_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "prefs.json"
            store = PreferenceStore(path)
            
            # Non-existent key should return default
            assert store.get("nonexistent", "default") == "default"

    def test_set_and_get(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "prefs.json"
            store = PreferenceStore(path)
            
            store.set("test_key", "test_value")
            assert store.get("test_key") == "test_value"

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "prefs.json"
            store = PreferenceStore(path)
            store.set("key", "value")
            
            # Reload
            store2 = PreferenceStore(path)
            assert store2.get("key") == "value"

    def test_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "prefs.json"
            store = PreferenceStore(path)
            store.set("key1", "value1")
            store.set("key2", "value2")
            
            snap = store.snapshot()
            assert snap["key1"] == "value1"
            assert snap["key2"] == "value2"

    def test_user_facts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "prefs.json"
            store = PreferenceStore(path)
            
            store.set("user_facts", {"name": "Alice", "city": "Seattle"})
            facts = store.get("user_facts")
            assert facts["name"] == "Alice"
            assert facts["city"] == "Seattle"


class TestShortTermMemory:
    """Test ShortTermMemory class."""

    def test_initialization(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "short_term.db"
            memory = ShortTermMemory(path)
            assert memory is not None

    def test_record_and_get(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "short_term.db"
            memory = ShortTermMemory(path)
            
            memory.record(
                transcript="Hello",
                intent="greeting",
                sub_intent="hello",
                capability="",
                arguments={},
                result="Hi there!",
                status="success",
                entities={}
            )
            
            recent = memory.get_recent(limit=5)
            assert len(recent) == 1
            assert recent[0]["transcript"] == "Hello"

    def test_get_recent_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "short_term.db"
            memory = ShortTermMemory(path)
            
            for i in range(10):
                memory.record(
                    transcript=f"Message {i}",
                    intent="test",
                    sub_intent="test",
                    capability="",
                    arguments={},
                    result="OK",
                    status="success",
                    entities={}
                )
            
            recent = memory.get_recent(limit=3)
            assert len(recent) == 3

    def test_clear(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "short_term.db"
            memory = ShortTermMemory(path)
            
            memory.record(
                transcript="Test",
                intent="test",
                sub_intent="test",
                capability="",
                arguments={},
                result="OK",
                status="success",
                entities={}
            )
            
            memory.clear()
            recent = memory.get_recent()
            assert len(recent) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])